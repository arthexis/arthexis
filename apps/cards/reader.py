import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as datetime_timezone
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.cards.actions import dispatch_rfid_action
from apps.cards.classic_layout import (
    CardLayoutError,
    FACTORY_KEY,
    FIRST_TRAIT_SECTOR,
    LAST_TRAIT_SECTOR,
    build_sector_trailer,
    build_trait_block_payloads,
    decode_transport_metadata,
    decode_traits_from_dump,
    default_writer_id,
    encode_lcd_label,
    encode_writer_date,
    encode_writer_id,
    ensure_sector_key_records,
    first_empty_trait_sector,
    managed_sector_numbers,
    normalize_lcd_label,
    normalize_sector_keys,
    normalize_trait_key,
    normalize_trait_records,
    normalize_trait_value,
    normalize_writer_id,
    scan_block_count,
    sector_block,
    sector_data_blocks,
    sector_key_record,
    sector_trailer_block,
    trait_sector_pairs,
    trait_sigils,
    zero_block,
)
from apps.cards.models import RFID
from apps.core.notifications import notify_async
from apps.video.rfid import queue_camera_snapshot

from .constants import (
    DEFAULT_RST_PIN,
    GPIO_PIN_MODE_BCM,
    SPI_BUS,
    SPI_DEVICE,
)
from .utils import convert_endianness_value, normalize_endianness

logger = logging.getLogger(__name__)

_deep_read_enabled: bool = False

_HEX_RE = re.compile(r"^[0-9A-F]+$")
_KEY_RE = re.compile(r"^[0-9A-F]{12}$")
_SPI_DEVICE_PATTERN = re.compile(r"(?:/dev/)?spidev(?P<bus>\d+)\.(?P<device>\d+)$")
_SPI_DEVICE_SHORT_PATTERN = re.compile(r"(?P<bus>\d+)\.(?P<device>\d+)$")


def _suppress_gpio_warnings() -> None:
    """Suppress expected GPIO reuse warnings before direct MFRC522 setup."""

    try:
        import RPi.GPIO as GPIO  # type: ignore
    except Exception:  # pragma: no cover - hardware dependent
        return

    setwarnings = getattr(GPIO, "setwarnings", None)
    if not callable(setwarnings):
        return

    try:
        setwarnings(False)
    except Exception:  # pragma: no cover - defensive hardware guard
        logger.debug("Unable to suppress GPIO warnings before RFID setup", exc_info=True)


def _normalize_command_text(value: object) -> str:
    """Strip trailing " %" tokens from each line of command output."""

    if not isinstance(value, str) or not value:
        return "" if value in (None, "") else str(value)

    lines: list[str] = []
    for segment in value.splitlines(keepends=True):
        newline = ""
        body = segment
        if segment.endswith("\r\n"):
            newline = "\r\n"
            body = segment[:-2]
        elif segment.endswith("\n") or segment.endswith("\r"):
            newline = segment[-1]
            body = segment[:-1]

        trimmed = body.rstrip()
        while trimmed.endswith(" %"):
            trimmed = trimmed[:-2].rstrip()

        lines.append(trimmed + newline)

    return "".join(lines)


COMMON_MIFARE_CLASSIC_KEYS = (
    FACTORY_KEY,
    "A0A1A2A3A4A5",
    "B0B1B2B3B4B5",
    "000000000000",
    "D3F7D3F7D3F7",
    "AABBCCDDEEFF",
    "1A2B3C4D5E6F",
    "4D3A99C351DD",
    "123456789ABC",
    "ABCDEF123456",
)


def _normalize_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    if not candidate:
        return None
    if not _KEY_RE.fullmatch(candidate):
        return None
    return candidate


def _key_to_bytes(value: str) -> list[int] | None:
    if not _KEY_RE.fullmatch(value):
        return None
    try:
        return [int(value[i : i + 2], 16) for i in range(0, 12, 2)]
    except ValueError:  # pragma: no cover - defensive guard
        return None


def _read_block(
    mfrc,
    *,
    block: int,
    key_type: str,
    key_bytes: list[int],
    uid: list[int],
) -> list[int] | None:
    if key_type == "B":
        auth_mode = mfrc.PICC_AUTHENT1B
    else:
        auth_mode = mfrc.PICC_AUTHENT1A
    status = mfrc.MFRC522_Auth(auth_mode, block, key_bytes, uid)
    if status != mfrc.MI_OK:
        return None
    read_status = mfrc.MFRC522_Read(block)
    if isinstance(read_status, tuple):
        read_status, data = read_status
    else:
        data = read_status
        read_status = mfrc.MI_OK
    if read_status != mfrc.MI_OK or data is None:
        return None
    return list(data)


def _is_sector_trailer_block(block: int) -> bool:
    if block < 0:
        return False
    if block < 128:
        return block % 4 == 3
    return (block - 128) % 16 == 15


def _write_block(
    mfrc,
    *,
    block: int,
    key_type: str,
    key_bytes: list[int],
    uid: list[int],
    data: list[int],
) -> bool:
    if key_type == "B":
        auth_mode = mfrc.PICC_AUTHENT1B
    else:
        auth_mode = mfrc.PICC_AUTHENT1A
    status = mfrc.MFRC522_Auth(auth_mode, block, key_bytes, uid)
    if status != mfrc.MI_OK:
        return False
    write_fn = getattr(mfrc, "MFRC522_Write", None)
    if not callable(write_fn):
        return False
    expected_data = list(data)[:16]
    write_status = write_fn(block, expected_data)
    if isinstance(write_status, tuple):
        write_status = write_status[0]
    if write_status == mfrc.MI_OK:
        return True
    if write_status is None:
        if _is_sector_trailer_block(block):
            return True
        readback = _read_block(
            mfrc,
            block=block,
            key_type=key_type,
            key_bytes=key_bytes,
            uid=uid,
        )
        return readback is not None and readback[:16] == expected_data
    return False


def _with_detected_rfid_card(timeout: float, handler) -> dict:
    try:
        from mfrc522 import MFRC522  # type: ignore

        spi_bus, spi_device = resolve_spi_bus_device()
        _suppress_gpio_warnings()
        mfrc = MFRC522(
            bus=spi_bus,
            device=spi_device,
            pin_mode=GPIO_PIN_MODE_BCM,
            pin_rst=DEFAULT_RST_PIN,
        )
    except Exception as exc:  # pragma: no cover - hardware dependent
        payload = {"error": str(exc)}
        errno_value = getattr(exc, "errno", None)
        if errno_value is not None:
            payload["errno"] = errno_value
        return payload

    selected = False
    try:
        end = time.time() + timeout
        while time.time() < end:  # pragma: no cover - hardware loop
            status, _tag_type = mfrc.MFRC522_Request(mfrc.PICC_REQIDL)
            if status != mfrc.MI_OK:
                continue
            status, uid = mfrc.MFRC522_Anticoll()
            if status != mfrc.MI_OK or not uid:
                continue
            try:
                selected = bool(mfrc.MFRC522_SelectTag(uid))
            except Exception:
                selected = False
            rfid = "".join(f"{x:02X}" for x in uid)
            return handler(mfrc, uid, rfid)
        return {"error": "No RFID card detected"}
    except Exception as exc:  # pragma: no cover - hardware dependent
        payload = {"error": str(exc)}
        errno_value = getattr(exc, "errno", None)
        if errno_value is not None:
            payload["errno"] = errno_value
        return payload
    finally:  # pragma: no cover - cleanup hardware
        if "mfrc" in locals() and mfrc is not None and selected:
            try:
                mfrc.MFRC522_StopCrypto1()
            except Exception:
                pass


def read_rfid_cell_value(
    *,
    block: int,
    offset: int,
    key: str,
    key_type: str = "A",
    timeout: float = 1.0,
) -> dict:
    """Read a single byte from the specified RFID block and offset."""

    key_bytes = _key_to_bytes(_normalize_key(key) or "")
    if not key_bytes:
        return {"error": "Invalid RFID key"}

    def _read(mfrc, uid, rfid):
        data = _read_block(
            mfrc, block=block, key_type=key_type.upper(), key_bytes=key_bytes, uid=uid
        )
        if data is None:
            return {"error": "Unable to read RFID block", "rfid": rfid}
        if offset < 0 or offset >= len(data):
            return {"error": "Invalid offset", "rfid": rfid}
        return {"rfid": rfid, "block": block, "offset": offset, "value": data[offset]}

    return _with_detected_rfid_card(timeout, _read)


def write_rfid_cell_value(
    *,
    block: int,
    offset: int,
    value: str,
    key: str,
    key_type: str = "A",
    timeout: float = 1.0,
) -> dict:
    """Write a single byte to the specified RFID block and offset."""

    key_bytes = _key_to_bytes(_normalize_key(key) or "")
    if not key_bytes:
        return {"error": "Invalid RFID key"}
    if not isinstance(value, str):
        return {"error": "Invalid RFID value"}
    normalized_value = value.strip().upper()
    if len(normalized_value) != 2:
        return {"error": "Invalid RFID value"}
    try:
        byte_value = int(normalized_value, 16)
    except ValueError:
        return {"error": "Invalid RFID value"}

    def _write(mfrc, uid, rfid):
        data = _read_block(
            mfrc, block=block, key_type=key_type.upper(), key_bytes=key_bytes, uid=uid
        )
        if data is None:
            return {"error": "Unable to read RFID block", "rfid": rfid}
        if offset < 0 or offset >= len(data):
            return {"error": "Invalid offset", "rfid": rfid}
        data[offset] = byte_value
        success = _write_block(
            mfrc,
            block=block,
            key_type=key_type.upper(),
            key_bytes=key_bytes,
            uid=uid,
            data=data,
        )
        if not success:
            return {"error": "Unable to write RFID block", "rfid": rfid}
        return {
            "rfid": rfid,
            "block": block,
            "offset": offset,
            "value": normalized_value,
        }

    return _with_detected_rfid_card(timeout, _write)


def _sector_for_block(block: int) -> int:
    return block // 4 if block < 128 else 32 + ((block - 128) // 16)


def _write_candidates_for_sector(tag, sector: int) -> list[tuple[str, str, list[int]]]:
    candidates: list[tuple[str, str, list[int]]] = []
    seen: set[tuple[str, str]] = set()

    def add(key_type: str, value: str | None) -> None:
        normalized = _normalize_key(value or "")
        if not normalized:
            return
        identity = (key_type, normalized)
        if identity in seen:
            return
        key_bytes = _key_to_bytes(normalized)
        if key_bytes is None:
            return
        seen.add(identity)
        candidates.append((key_type, normalized, key_bytes))

    if sector in (0, 1):
        add("A", FACTORY_KEY)
        return candidates

    record = sector_key_record(getattr(tag, "sector_keys", {}), sector)
    if record:
        add("A", record.get("key_a"))
        add("B", record.get("key_b"))
    add("A", getattr(tag, "key_a", ""))
    add("B", getattr(tag, "key_b", ""))
    add("A", FACTORY_KEY)
    add("B", FACTORY_KEY)
    return candidates


def _write_block_with_candidates(mfrc, tag, uid: list[int], block: int, data: list[int]) -> tuple[bool, str | None]:
    sector = _sector_for_block(block)
    for key_type, key_value, key_bytes in _write_candidates_for_sector(tag, sector):
        if _write_block(
            mfrc,
            block=block,
            key_type=key_type,
            key_bytes=key_bytes,
            uid=uid,
            data=list(data)[:16],
        ):
            return True, key_value
    return False, None


def _write_writer_metadata(
    mfrc,
    tag,
    uid: list[int],
    *,
    writer_id: str | None = None,
    written_at=None,
) -> tuple[dict[str, Any], set[str]]:
    normalized_writer = normalize_writer_id(writer_id or default_writer_id())
    timestamp = written_at or timezone.now()
    payloads = {
        sector_block(1, 1): encode_writer_id(normalized_writer),
        sector_block(1, 2): encode_writer_date(timestamp),
    }
    errors: list[str] = []
    for block, data in payloads.items():
        success, _used_key = _write_block_with_candidates(mfrc, tag, uid, block, data)
        if not success:
            errors.append(f"block {block}")
    updates: set[str] = set()
    if not errors:
        tag.writer_id = normalized_writer
        tag.writer_written_at = timestamp
        updates.update({"writer_id", "writer_written_at"})
    return {"writer_id": normalized_writer, "errors": errors}, updates


def _initialize_detected_card(
    mfrc,
    uid: list[int],
    rfid: str,
    *,
    tag=None,
    writer_id: str | None = None,
) -> dict[str, Any]:
    if tag is None:
        tag, _created = RFID.register_scan(rfid, kind=RFID.CLASSIC)
    existing_records = dict(getattr(tag, "sector_keys", {}) or {})
    records = ensure_sector_key_records(existing_records)
    now = timezone.now()
    writer_result, writer_updates = _write_writer_metadata(
        mfrc,
        tag,
        uid,
        writer_id=writer_id,
        written_at=now,
    )
    errors: list[dict[str, Any]] = []
    initialized_sectors: list[int] = []
    persisted_records = dict(existing_records)
    expected_sectors = list(managed_sector_numbers())

    for sector in expected_sectors:
        record = records.get(str(sector))
        if not record:
            continue
        sector_errors: list[str] = []
        for block in sector_data_blocks(sector):
            success, _used_key = _write_block_with_candidates(
                mfrc,
                tag,
                uid,
                block,
                zero_block(),
            )
            if not success:
                sector_errors.append(f"block {block}")
        if sector_errors:
            errors.append({"sector": sector, "errors": sector_errors})
            continue
        trailer = build_sector_trailer(record["key_a"], record["key_b"])
        trailer_block = sector_trailer_block(sector)
        success, _used_key = _write_block_with_candidates(
            mfrc,
            tag,
            uid,
            trailer_block,
            trailer,
        )
        if not success:
            sector_errors.append(f"trailer {trailer_block}")
        if sector_errors:
            errors.append({"sector": sector, "errors": sector_errors})
            continue
        initialized_sectors.append(sector)
        persisted_records[str(sector)] = record

    updates = set(writer_updates)
    if initialized_sectors:
        tag.sector_keys = normalize_sector_keys(persisted_records)
        updates.add("sector_keys")
        if any(
            FIRST_TRAIT_SECTOR <= sector <= LAST_TRAIT_SECTOR
            for sector in initialized_sectors
        ):
            tag.traits = {}
            updates.add("traits")
    fully_initialized = bool(expected_sectors) and initialized_sectors == expected_sectors
    if fully_initialized and not errors:
        tag.initialized_on = now
        updates.add("initialized_on")
    if updates:
        tag.save(update_fields=sorted(updates))

    return {
        "rfid": rfid,
        "label_id": tag.pk,
        "initialized": fully_initialized and not errors,
        "initialized_sectors": initialized_sectors,
        "writer": writer_result,
        "errors": errors,
    }


def initialize_current_card(
    *,
    timeout: float = 2.0,
    writer_id: str | None = None,
) -> dict[str, Any]:
    """Initialize managed sectors on the presented MIFARE Classic card."""

    def _initialize(mfrc, uid, rfid):
        tag, _created = RFID.register_scan(rfid, kind=RFID.CLASSIC)
        return _initialize_detected_card(
            mfrc,
            uid,
            rfid,
            tag=tag,
            writer_id=writer_id,
        )

    return _with_detected_rfid_card(timeout, _initialize)


def write_current_card_lcd_label(
    *,
    label: str,
    timeout: float = 2.0,
    writer_id: str | None = None,
) -> dict[str, Any]:
    """Write the sector-0 LCD label to the presented card."""

    try:
        normalized_label = normalize_lcd_label(label)
    except CardLayoutError as exc:
        return {"error": str(exc)}
    encoded = encode_lcd_label(normalized_label)
    payloads = {
        sector_block(0, 1): encoded[:16],
        sector_block(0, 2): encoded[16:32],
    }

    def _write(mfrc, uid, rfid):
        tag, _created = RFID.register_scan(rfid, kind=RFID.CLASSIC)
        errors: list[str] = []
        for block, data in payloads.items():
            success, _used_key = _write_block_with_candidates(
                mfrc,
                tag,
                uid,
                block,
                data,
            )
            if not success:
                errors.append(f"block {block}")
        writer_result, writer_updates = _write_writer_metadata(
            mfrc,
            tag,
            uid,
            writer_id=writer_id,
        )
        if errors:
            return {"error": "Unable to write LCD label", "rfid": rfid, "errors": errors}
        tag.lcd_label = normalized_label
        update_fields = {"lcd_label", *writer_updates}
        tag.save(update_fields=sorted(update_fields))
        return {
            "rfid": rfid,
            "label_id": tag.pk,
            "lcd_label": normalized_label,
            "writer": writer_result,
        }

    return _with_detected_rfid_card(timeout, _write)


def set_current_card_trait(
    *,
    key: str,
    value: str,
    timeout: float = 2.0,
    writer_id: str | None = None,
    initialize: bool = True,
) -> dict[str, Any]:
    """Add or update a trait on the presented card."""

    try:
        normalized_key = normalize_trait_key(key)
        normalized_value = normalize_trait_value(value)
    except CardLayoutError as exc:
        return {"error": str(exc)}

    def _write(mfrc, uid, rfid):
        tag, _created = RFID.register_scan(rfid, kind=RFID.CLASSIC)
        if initialize and not getattr(tag, "initialized_on", None):
            init_result = _initialize_detected_card(
                mfrc,
                uid,
                rfid,
                tag=tag,
                writer_id=writer_id,
            )
            if init_result.get("errors") or not init_result.get("initialized"):
                return {
                    "error": "Unable to initialize RFID card before writing trait",
                    "rfid": rfid,
                    "initialization": init_result,
                }
            tag.refresh_from_db()

        records = normalize_trait_records(getattr(tag, "traits", {}))
        record = records.get(normalized_key)
        start_sector = record.get("sector") if isinstance(record, dict) else None
        if start_sector is None:
            start_sector = first_empty_trait_sector(records)
        if start_sector is None:
            return {"error": "No empty RFID trait sector pair available", "rfid": rfid}

        writer_result, writer_updates = _write_writer_metadata(
            mfrc,
            tag,
            uid,
            writer_id=writer_id,
        )
        block_payloads = build_trait_block_payloads(
            int(start_sector),
            normalized_key,
            normalized_value,
        )
        errors: list[str] = []
        for block, block_data in block_payloads.items():
            success, _used_key = _write_block_with_candidates(
                mfrc,
                tag,
                uid,
                block,
                block_data,
            )
            if not success:
                errors.append(f"block {block}")
        if errors:
            return {"error": "Unable to write RFID trait", "rfid": rfid, "errors": errors}

        records[normalized_key] = {
            "value": normalized_value,
            "sector": int(start_sector),
            "sectors": [int(start_sector), int(start_sector) + 1],
            "updated_at": timezone.now().isoformat(),
        }
        tag.traits = normalize_trait_records(records)
        update_fields = {"traits", *writer_updates}
        tag.save(update_fields=sorted(update_fields))
        return {
            "rfid": rfid,
            "label_id": tag.pk,
            "trait": normalized_key,
            "value": normalized_value,
            "sector": int(start_sector),
            "writer": writer_result,
        }

    return _with_detected_rfid_card(timeout, _write)


def _build_key_candidates(
    tag, key_attr: str, verified_attr: str
) -> list[tuple[str, list[int]]]:
    candidates: list[tuple[str, list[int]]] = []
    seen: set[str] = set()

    normalized = _normalize_key(getattr(tag, key_attr, ""))
    if normalized:
        bytes_key = _key_to_bytes(normalized)
        if bytes_key is not None:
            candidates.append((normalized, bytes_key))
            seen.add(normalized)

    if not bool(getattr(tag, verified_attr, False)):
        for key in COMMON_MIFARE_CLASSIC_KEYS:
            if key in seen:
                continue
            bytes_key = _key_to_bytes(key)
            if bytes_key is None:
                continue
            candidates.append((key, bytes_key))
            seen.add(key)

    if not candidates:
        fallback = COMMON_MIFARE_CLASSIC_KEYS[0]
        bytes_key = _key_to_bytes(fallback)
        if bytes_key is not None:
            candidates.append((fallback, bytes_key))

    return candidates


def _build_sector_key_candidates(
    tag,
    sector: int,
    key_type: str,
) -> list[tuple[str, list[int]]]:
    candidates: list[tuple[str, list[int]]] = []
    seen: set[str] = set()
    key_name = "key_a" if key_type == "A" else "key_b"

    if sector in (0, 1):
        factory_bytes = _key_to_bytes(FACTORY_KEY)
        if factory_bytes is not None:
            candidates.append((FACTORY_KEY, factory_bytes))
            seen.add(FACTORY_KEY)

    sector_record = sector_key_record(getattr(tag, "sector_keys", {}), sector)
    if sector_record:
        sector_key = sector_record.get(key_name)
        key_bytes = _key_to_bytes(sector_key or "")
        if sector_key and key_bytes is not None and sector_key not in seen:
            candidates.append((sector_key, key_bytes))
            seen.add(sector_key)

    fallback_attr = "key_a" if key_type == "A" else "key_b"
    fallback_verified = "key_a_verified" if key_type == "A" else "key_b_verified"
    for key_value, key_bytes in _build_key_candidates(
        tag, fallback_attr, fallback_verified
    ):
        if key_value in seen:
            continue
        candidates.append((key_value, key_bytes))
        seen.add(key_value)

    if FACTORY_KEY not in seen:
        factory_bytes = _key_to_bytes(FACTORY_KEY)
        if factory_bytes is not None:
            candidates.append((FACTORY_KEY, factory_bytes))

    return candidates


def resolve_spi_bus_device(
    override: str | None = None,
    *,
    log_invalid: bool = True,
) -> tuple[int, int]:
    value = override if override is not None else os.environ.get("RFID_SPI_DEVICE", "")
    cleaned = value.strip()
    if cleaned:
        match = _SPI_DEVICE_PATTERN.search(
            cleaned
        ) or _SPI_DEVICE_SHORT_PATTERN.fullmatch(cleaned)
        if match:
            return int(match["bus"]), int(match["device"])
        if cleaned.isdigit():
            return SPI_BUS, int(cleaned)
        if log_invalid:
            logger.warning(
                "RFID_SPI_DEVICE override %r is not recognized; using default SPI device "
                "/dev/spidev%s.%s",
                cleaned,
                SPI_BUS,
                SPI_DEVICE,
            )
    return SPI_BUS, SPI_DEVICE


def resolve_spi_device_path(
    override: str | None = None,
    *,
    log_invalid: bool = True,
) -> Path:
    bus, device = resolve_spi_bus_device(override, log_invalid=log_invalid)
    return Path(f"/dev/spidev{bus}.{device}")


def _build_tag_response(
    tag, rfid: str, *, created: bool, kind: str | None = None
) -> dict:
    """Update metadata and build the standard RFID response payload."""

    updates = set()
    if kind and tag.kind != kind:
        tag.kind = kind
        updates.add("kind")
    tag.last_seen_on = timezone.now()
    updates.add("last_seen_on")
    if updates:
        tag.save(update_fields=sorted(updates))
    allowed = bool(tag.allowed)
    action_details: dict[str, object] | None = None
    validation_action = dispatch_rfid_action(
        action_id=getattr(tag, "validation_action", ""),
        rfid=rfid,
        tag=tag,
        phase="reader_validation",
    )
    if getattr(tag, "validation_action", ""):
        action_details = {
            "output": _normalize_command_text(validation_action.output),
            "error": _normalize_command_text(validation_action.error),
            "success": bool(validation_action.success),
        }
    allowed = allowed and validation_action.success

    if allowed:
        post_action = dispatch_rfid_action(
            action_id=getattr(tag, "post_auth_action", ""),
            rfid=rfid,
            tag=tag,
            phase="reader_success",
        )
        if getattr(tag, "post_auth_action", ""):
            if action_details is None:
                action_details = {}
            action_details["post_output"] = _normalize_command_text(post_action.output)
            action_details["post_error"] = _normalize_command_text(post_action.error)
            action_details["post_success"] = bool(post_action.success)

    result = {
        "rfid": rfid,
        "label_id": tag.pk,
        "custom_label": getattr(tag, "custom_label", ""),
        "lcd_label": getattr(tag, "lcd_label", ""),
        "created": created,
        "color": tag.color,
        "allowed": allowed,
        "released": tag.released,
        "reference": tag.reference.value if tag.reference else None,
        "kind": tag.kind,
        "endianness": tag.endianness,
        "initialized": bool(getattr(tag, "initialized_on", None)),
        "initialized_on": tag.initialized_on.isoformat()
        if getattr(tag, "initialized_on", None)
        else None,
    }
    if getattr(tag, "traits", None):
        result["traits"] = tag.trait_values()
        result["trait_sigils"] = tag.trait_sigils()
    if action_details is not None:
        result["action_output"] = action_details
    status_text = "OK" if allowed else "BAD"
    color_word = (tag.color or "").upper()
    notify_async(
        f"RFID {tag.label_id} {status_text}".strip(), f"{rfid} {color_word}".strip()
    )
    queue_camera_snapshot(rfid, result)
    return result


@dataclass
class ReaderStrategy:
    """RFID reader access details for a single read session.

    Attributes:
        mfrc: Active MFRC522-compatible reader instance.
        cleanup_gpio: Whether GPIO cleanup should run on exit.
        source: Human-readable strategy label for diagnostics/tests.
    """

    mfrc: Any
    cleanup_gpio: bool
    source: str


def _build_error_payload(exc: Exception) -> dict:
    """Convert hardware exceptions into a consistent response payload."""

    payload = {"error": str(exc)}
    errno_value = getattr(exc, "errno", None)
    if errno_value is not None:
        payload["errno"] = errno_value
    return payload


def _init_provided_reader_strategy(
    mfrc,
    *,
    cleanup: bool,
) -> tuple[ReaderStrategy | None, dict | None]:
    """Wrap an injected reader instance in a strategy payload."""

    if mfrc is None:
        return None, {"error": "RFID reader instance is required"}
    return ReaderStrategy(mfrc=mfrc, cleanup_gpio=cleanup, source="provided"), None


def _init_default_reader_strategy(
    *,
    cleanup: bool,
) -> tuple[ReaderStrategy | None, dict | None]:
    """Create the default MFRC522 reader strategy from local hardware settings."""

    try:
        from mfrc522 import MFRC522  # type: ignore

        spi_bus, spi_device = resolve_spi_bus_device()
        _suppress_gpio_warnings()
        reader = MFRC522(
            bus=spi_bus,
            device=spi_device,
            pin_mode=GPIO_PIN_MODE_BCM,
            pin_rst=DEFAULT_RST_PIN,
        )
    except Exception as exc:  # pragma: no cover - hardware dependent
        return None, _build_error_payload(exc)
    return ReaderStrategy(mfrc=reader, cleanup_gpio=cleanup, source="default"), None


def _initialize_reader_strategy(
    mfrc=None,
    *,
    cleanup: bool = True,
) -> tuple[ReaderStrategy | None, dict | None]:
    """Return a reader strategy or a specific initialization failure payload."""

    if mfrc is not None:
        return _init_provided_reader_strategy(mfrc, cleanup=cleanup)
    return _init_default_reader_strategy(cleanup=cleanup)


def _sleep_between_read_attempts(
    *,
    use_irq: bool,
    poll_interval: float | None,
) -> None:
    """Apply retry delay rules for polling-based reads."""

    if use_irq:
        return
    if poll_interval:
        time.sleep(poll_interval)


def _read_card_via_polling(
    strategy: ReaderStrategy,
    *,
    timeout: float,
    poll_interval: float | None,
    use_irq: bool,
) -> tuple[list[int], bool] | None:
    """Poll the reader until a UID is detected or the timeout expires."""

    end = time.time() + timeout
    while time.time() < end:  # pragma: no cover - hardware loop
        status, _tag_type = strategy.mfrc.MFRC522_Request(strategy.mfrc.PICC_REQIDL)
        if status == strategy.mfrc.MI_OK:
            status, uid = strategy.mfrc.MFRC522_Anticoll()
            if status == strategy.mfrc.MI_OK:
                uid_bytes = list(uid or [])
                selected = False
                try:
                    if uid_bytes:
                        selected = bool(strategy.mfrc.MFRC522_SelectTag(uid_bytes))
                except Exception:
                    selected = False
                return uid_bytes, selected
        _sleep_between_read_attempts(use_irq=use_irq, poll_interval=poll_interval)
    return None


def _decode_scanned_rfid(uid_bytes: list[int]) -> dict:
    """Normalize raw UID bytes into the RFID payload used by scanner flows."""

    rfid = "".join(f"{value:02X}" for value in uid_bytes)
    kind = RFID.NTAG215 if len(uid_bytes) > 5 else RFID.CLASSIC
    return {"uid": uid_bytes, "rfid": rfid, "kind": kind}


def _read_basic_tag_data(decoded_card: dict) -> tuple[RFID, bool, dict]:
    """Register a scanned card and build the standard response payload."""

    tag, created = RFID.register_scan(decoded_card["rfid"], kind=decoded_card["kind"])
    result = _build_tag_response(
        tag,
        decoded_card["rfid"],
        created=created,
        kind=decoded_card["kind"],
    )
    return tag, created, result


def _save_tag_layout_metadata(tag, metadata: dict[str, Any]) -> None:
    updates: set[str] = set()
    if "lcd_label" in metadata:
        lcd_label = normalize_lcd_label(metadata.get("lcd_label", ""))
        if getattr(tag, "lcd_label", "") != lcd_label:
            tag.lcd_label = lcd_label
            updates.add("lcd_label")

    if "writer" in metadata:
        writer = metadata.get("writer") if isinstance(metadata.get("writer"), dict) else {}
        writer_id = str(writer.get("id") or "").strip()[:16]
        if getattr(tag, "writer_id", "") != writer_id:
            tag.writer_id = writer_id
            updates.add("writer_id")

        writer_date = str(writer.get("written_at") or "").strip()
        parsed = _parse_writer_timestamp(writer_date) if writer_date else None
        if getattr(tag, "writer_written_at", None) != parsed:
            tag.writer_written_at = parsed
            updates.add("writer_written_at")

    if updates:
        tag.save(update_fields=sorted(updates))


def _parse_writer_timestamp(value: str):
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=datetime_timezone.utc
        )
    except ValueError:
        return None


def _apply_transport_metadata_to_result(metadata: dict[str, Any], result: dict) -> None:
    if "lcd_label" in metadata:
        result["lcd_label"] = normalize_lcd_label(metadata.get("lcd_label", ""))
    if "writer" not in metadata:
        return
    writer = metadata.get("writer")
    if isinstance(writer, dict) and (writer.get("id") or writer.get("written_at")):
        result["writer"] = writer
    else:
        result.pop("writer", None)


def _transport_block_numbers() -> tuple[int, int, int, int]:
    return (
        sector_block(0, 1),
        sector_block(0, 2),
        sector_block(1, 1),
        sector_block(1, 2),
    )


def _read_transport_layout(mfrc, uid: list[int]) -> dict[str, Any]:
    factory = _key_to_bytes(FACTORY_KEY)
    if factory is None:
        return {}
    dump: list[dict[str, Any]] = []
    transport_blocks = _transport_block_numbers()
    for block in transport_blocks:
        try:
            data = _read_block(
                mfrc,
                block=block,
                key_type="A",
                key_bytes=factory,
                uid=uid,
            )
        except Exception:
            data = None
        if data is not None:
            dump.append({"block": block, "data": data, "key": "A"})
    if len(dump) != len(transport_blocks):
        return {}
    return decode_transport_metadata(dump)


def _enrich_transport_layout(mfrc, tag, uid: list[int], result: dict) -> dict:
    metadata = _read_transport_layout(mfrc, uid)
    if not metadata:
        return result
    _apply_transport_metadata_to_result(metadata, result)
    _save_tag_layout_metadata(tag, metadata)
    return result


def _dump_block_numbers(dump: list[dict[str, Any]]) -> set[int]:
    blocks: set[int] = set()
    for entry in dump:
        if not isinstance(entry, dict):
            continue
        block = entry.get("block")
        data = entry.get("data")
        if isinstance(block, int) and isinstance(data, (list, tuple)) and len(data) >= 16:
            blocks.add(block)
    return blocks


def _trait_dump_is_complete(dump: list[dict[str, Any]]) -> bool:
    expected_blocks: set[int] = set()
    for start_sector, continuation_sector in trait_sector_pairs():
        expected_blocks.update(sector_data_blocks(start_sector))
        expected_blocks.update(sector_data_blocks(continuation_sector))
    return expected_blocks.issubset(_dump_block_numbers(dump))


def _transport_dump_is_complete(dump: list[dict[str, Any]]) -> bool:
    return set(_transport_block_numbers()).issubset(_dump_block_numbers(dump))


def _save_tag_traits_from_dump(tag, dump: list[dict[str, Any]], result: dict) -> None:
    if not _trait_dump_is_complete(dump):
        return
    traits = decode_traits_from_dump(dump)
    normalized = normalize_trait_records(traits)
    if normalized != getattr(tag, "traits", {}):
        tag.traits = normalized
        tag.save(update_fields=["traits"])
    if not normalized:
        result.pop("traits", None)
        result.pop("trait_sigils", None)
        return
    result["traits"] = {
        key: str(record.get("value", "")) for key, record in normalized.items()
    }
    result["trait_sigils"] = trait_sigils(normalized)


def _read_deep_classic_tag_data(mfrc, tag, uid: list[int], result: dict) -> dict:
    """Enrich a classic-tag response with authenticated block dump data."""

    keys: dict[str, object] = {}
    if hasattr(tag, "key_a"):
        key_a_value = _normalize_key(getattr(tag, "key_a", ""))
        keys["a"] = key_a_value or (getattr(tag, "key_a", "") or "")
        keys["a_verified"] = bool(getattr(tag, "key_a_verified", False))
    if hasattr(tag, "key_b"):
        key_b_value = _normalize_key(getattr(tag, "key_b", ""))
        keys["b"] = key_b_value or (getattr(tag, "key_b", "") or "")
        keys["b_verified"] = bool(getattr(tag, "key_b_verified", False))

    result["keys"] = keys
    result["deep_read"] = True

    dump = []
    pending_updates: set[str] = set()
    last_sector = -1
    key_candidates: dict[str, list[tuple[str, list[int]]]] = {"A": [], "B": []}
    for block in range(scan_block_count()):
        sector = _sector_for_block(block)
        if sector != last_sector:
            key_candidates = {
                "A": _build_sector_key_candidates(tag, sector, "A"),
                "B": _build_sector_key_candidates(tag, sector, "B"),
            }
            last_sector = sector
        try:
            used_key = None
            used_value = None
            status = mfrc.MI_ERR

            for index, (key_value, key_bytes) in enumerate(key_candidates["A"]):
                status = mfrc.MFRC522_Auth(mfrc.PICC_AUTHENT1A, block, key_bytes, uid)
                if status == mfrc.MI_OK:
                    used_key = "A"
                    used_value = key_value
                    if index > 0:
                        key_candidates["A"].insert(0, key_candidates["A"].pop(index))
                    break

            if status != mfrc.MI_OK:
                for index, (key_value, key_bytes) in enumerate(key_candidates["B"]):
                    status = mfrc.MFRC522_Auth(
                        mfrc.PICC_AUTHENT1B,
                        block,
                        key_bytes,
                        uid,
                    )
                    if status == mfrc.MI_OK:
                        used_key = "B"
                        used_value = key_value
                        if index > 0:
                            key_candidates["B"].insert(0, key_candidates["B"].pop(index))
                        break

            if status == mfrc.MI_OK:
                read_status = mfrc.MFRC522_Read(block)
                if isinstance(read_status, tuple):
                    read_state, data = read_status
                else:
                    read_state, data = (mfrc.MI_OK, read_status)
                if read_state == mfrc.MI_OK and data is not None:
                    entry = {"block": block, "data": list(data)}
                    if used_key:
                        entry["key"] = used_key
                    dump.append(entry)

                    if used_key == "A" and used_value:
                        if used_value != keys.get("a"):
                            keys["a"] = used_value
                        if not keys.get("a_verified"):
                            keys["a_verified"] = True
                        if (
                            not getattr(tag, "key_a_verified", False)
                            or getattr(tag, "key_a", "").upper() != used_value
                        ):
                            setattr(tag, "key_a", used_value)
                            setattr(tag, "key_a_verified", True)
                            pending_updates.update({"key_a", "key_a_verified"})

                    if used_key == "B" and used_value:
                        if used_value != keys.get("b"):
                            keys["b"] = used_value
                        if not keys.get("b_verified"):
                            keys["b_verified"] = True
                        if (
                            not getattr(tag, "key_b_verified", False)
                            or getattr(tag, "key_b", "").upper() != used_value
                        ):
                            setattr(tag, "key_b", used_value)
                            setattr(tag, "key_b_verified", True)
                            pending_updates.update({"key_b", "key_b_verified"})
        except Exception as exc:
            logger.debug("Failed to read block %d for classic tag: %s", block, exc)
            continue

    if _transport_dump_is_complete(dump):
        metadata = decode_transport_metadata(dump)
        _apply_transport_metadata_to_result(metadata, result)
        _save_tag_layout_metadata(tag, metadata)
    _save_tag_traits_from_dump(tag, dump, result)

    if pending_updates:
        tag.save(update_fields=sorted(pending_updates))

    result["dump"] = dump
    if getattr(tag, "data", None) != dump:
        tag.data = [dict(entry) for entry in dump]
        tag.save(update_fields=["data"])
    return result


def _finalize_reader_session(
    strategy: ReaderStrategy,
    *,
    cleanup: bool,
    selected: bool,
) -> None:
    """Release crypto state and optionally cleanup GPIO for a read session."""

    if strategy.mfrc is not None and selected:
        try:
            strategy.mfrc.MFRC522_StopCrypto1()
        except Exception:
            pass

    if not cleanup or not strategy.cleanup_gpio:
        return

    try:
        import RPi.GPIO as GPIO  # pragma: no cover - hardware dependent
    except Exception:  # pragma: no cover - hardware dependent
        GPIO = None

    if GPIO:
        try:
            GPIO.cleanup()
        except Exception:
            pass


def enable_deep_read(duration: float | None = None) -> bool:
    """Enable deep read mode until it is explicitly disabled."""

    global _deep_read_enabled
    _deep_read_enabled = True
    return _deep_read_enabled


def disable_deep_read() -> bool:
    """Disable deep read mode and return the new state."""

    global _deep_read_enabled
    _deep_read_enabled = False
    return _deep_read_enabled


def deep_read_enabled() -> bool:
    """Return whether deep read mode is currently enabled."""

    return _deep_read_enabled


def toggle_deep_read() -> bool:
    """Toggle deep read mode and return the new state."""

    global _deep_read_enabled
    _deep_read_enabled = not _deep_read_enabled
    return _deep_read_enabled


def read_rfid(
    mfrc=None,
    cleanup: bool = True,
    timeout: float = 1.0,
    poll_interval: float | None = 0.05,
    use_irq: bool = False,
) -> dict:
    """Read a single RFID tag using the MFRC522 reader.

    Args:
        mfrc: Optional MFRC522 reader instance.
        cleanup: Whether to call ``GPIO.cleanup`` on exit.
        timeout: How long to poll for a card before giving up.
        poll_interval: Delay between polling attempts. Set to ``None`` or ``0``
            to skip sleeping (useful when hardware interrupts are configured).
        use_irq: If ``True``, do not sleep between polls regardless of
            ``poll_interval``.

    Returns:
        Scanner payload for the detected RFID tag or an error/empty payload.
    """

    strategy, failure = _initialize_reader_strategy(mfrc, cleanup=cleanup)
    if failure is not None:
        return failure
    assert strategy is not None

    selected = False
    rfid = None
    try:
        detected_card = _read_card_via_polling(
            strategy,
            timeout=timeout,
            poll_interval=poll_interval,
            use_irq=use_irq,
        )
        if detected_card is None:
            return {"rfid": None, "label_id": None}

        uid_bytes, selected = detected_card
        decoded_card = _decode_scanned_rfid(uid_bytes)
        rfid = decoded_card["rfid"]
        tag, _created, result = _read_basic_tag_data(decoded_card)

        if tag.kind == RFID.CLASSIC:
            result = _enrich_transport_layout(strategy.mfrc, tag, uid_bytes, result)
            if _deep_read_enabled:
                return _read_deep_classic_tag_data(strategy.mfrc, tag, uid_bytes, result)
        return result
    except Exception as exc:  # pragma: no cover - hardware dependent
        if rfid:
            notify_async(f"RFID {rfid}", "Read failed")
        return _build_error_payload(exc)
    finally:  # pragma: no cover - cleanup hardware
        _finalize_reader_session(strategy, cleanup=cleanup, selected=selected)


def validate_rfid_value(
    value: object, *, kind: str | None = None, endianness: str | None = None
) -> dict:
    """Validate ``value`` against the database and return scanner payload data."""

    if not isinstance(value, str):
        if value is None:
            return {"error": "RFID value is required"}
        return {"error": "RFID must be a string"}

    if not value:
        return {"error": "RFID value is required"}

    normalized = value.strip().upper()
    if not normalized:
        return {"error": "RFID value is required"}
    if not _HEX_RE.fullmatch(normalized):
        return {"error": "RFID must be hexadecimal digits"}

    normalized_kind = None
    if isinstance(kind, str):
        candidate = kind.strip().upper()
        if candidate in {choice[0] for choice in RFID.KIND_CHOICES}:
            normalized_kind = candidate

    normalized_endianness = normalize_endianness(endianness)
    converted_value = convert_endianness_value(
        normalized,
        from_endianness=RFID.BIG_ENDIAN,
        to_endianness=normalized_endianness,
    )

    try:
        tag, created = RFID.register_scan(
            converted_value, kind=normalized_kind, endianness=normalized_endianness
        )
    except ValidationError as exc:
        return {"error": "; ".join(exc.messages)}
    except Exception as exc:  # pragma: no cover - defensive fallback
        return {"error": str(exc)}

    return _build_tag_response(
        tag, converted_value, created=created, kind=normalized_kind
    )
