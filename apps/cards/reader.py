import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from django.core.exceptions import ValidationError
from django.utils import timezone
from typing import Any

from apps.cards.models import RFID
from apps.cards.rfid_actions import RFIDActionContext
from apps.cards.rfid_actions import dispatch_post_auth_action
from apps.cards.rfid_actions import dispatch_pre_auth_action
from apps.core.notifications import notify_async

from .constants import (
    DEFAULT_RST_PIN,
    GPIO_PIN_MODE_BCM,
    SPI_BUS,
    SPI_DEVICE,
)
from apps.video.rfid import queue_camera_snapshot
from .utils import convert_endianness_value, normalize_endianness


logger = logging.getLogger(__name__)

_deep_read_enabled: bool = False

_HEX_RE = re.compile(r"^[0-9A-F]+$")
_KEY_RE = re.compile(r"^[0-9A-F]{12}$")
_SPI_DEVICE_PATTERN = re.compile(r"(?:/dev/)?spidev(?P<bus>\d+)\.(?P<device>\d+)$")
_SPI_DEVICE_SHORT_PATTERN = re.compile(r"(?P<bus>\d+)\.(?P<device>\d+)$")


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
    "FFFFFFFFFFFF",
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
    if read_status != mfrc.MI_OK or data is None:
        return None
    return list(data)


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
    write_status = write_fn(block, data)
    if isinstance(write_status, tuple):
        write_status = write_status[0]
    return write_status == mfrc.MI_OK


def _with_detected_rfid_card(timeout: float, handler) -> dict:
    try:
        from mfrc522 import MFRC522  # type: ignore

        spi_bus, spi_device = resolve_spi_bus_device()
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


def _build_key_candidates(tag, key_attr: str, verified_attr: str) -> list[tuple[str, list[int]]]:
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


def resolve_spi_bus_device(
    override: str | None = None,
    *,
    log_invalid: bool = True,
) -> tuple[int, int]:
    value = override if override is not None else os.environ.get("RFID_SPI_DEVICE", "")
    cleaned = value.strip()
    if cleaned:
        match = _SPI_DEVICE_PATTERN.search(cleaned) or _SPI_DEVICE_SHORT_PATTERN.fullmatch(
            cleaned
        )
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


def _build_tag_response(tag, rfid: str, *, created: bool, kind: str | None = None) -> dict:
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
    action_context = RFIDActionContext(tag=tag, rfid_value=rfid)
    command_details: dict[str, object] | None = None
    pre_auth_action = getattr(tag, "pre_auth_action", "")
    pre_auth_result = dispatch_pre_auth_action(pre_auth_action, context=action_context)
    if pre_auth_action:
        command_details = {
            "action": pre_auth_action,
            "allowed": pre_auth_result.allowed,
            "details": pre_auth_result.details,
            "error": _normalize_command_text(pre_auth_result.error),
        }
    allowed = allowed and pre_auth_result.allowed

    post_auth_action = getattr(tag, "post_auth_action", "")
    if allowed and post_auth_action:
        try:
            dispatch_post_auth_action(post_auth_action, context=action_context)
        except Exception:  # pragma: no cover - best effort fire and forget
            pass

    result = {
        "rfid": rfid,
        "label_id": tag.pk,
        "created": created,
        "color": tag.color,
        "allowed": allowed,
        "released": tag.released,
        "reference": tag.reference.value if tag.reference else None,
        "kind": tag.kind,
        "endianness": tag.endianness,
    }
    if command_details is not None:
        command_details["error"] = _normalize_command_text(
            command_details.get("error", "")
        )
        result["command_output"] = command_details
    status_text = "OK" if allowed else "BAD"
    color_word = (tag.color or "").upper()
    notify_async(f"RFID {tag.label_id} {status_text}".strip(), f"{rfid} {color_word}".strip())
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
    key_candidates = {
        "A": _build_key_candidates(tag, "key_a", "key_a_verified"),
        "B": _build_key_candidates(tag, "key_b", "key_b_verified"),
    }

    for block in range(64):
        try:
            used_key = None
            used_value = None
            used_bytes: list[int] | None = None
            status = mfrc.MI_ERR

            for key_value, key_bytes in key_candidates["A"]:
                status = mfrc.MFRC522_Auth(mfrc.PICC_AUTHENT1A, block, key_bytes, uid)
                if status == mfrc.MI_OK:
                    used_key = "A"
                    used_value = key_value
                    used_bytes = key_bytes
                    break

            if status != mfrc.MI_OK:
                for key_value, key_bytes in key_candidates["B"]:
                    status = mfrc.MFRC522_Auth(
                        mfrc.PICC_AUTHENT1B,
                        block,
                        key_bytes,
                        uid,
                    )
                    if status == mfrc.MI_OK:
                        used_key = "B"
                        used_value = key_value
                        used_bytes = key_bytes
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
                        if used_bytes is not None:
                            key_candidates["A"] = [(used_value, used_bytes)]

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
                        if used_bytes is not None:
                            key_candidates["B"] = [(used_value, used_bytes)]
        except Exception as exc:
            logger.debug("Failed to read block %d for classic tag: %s", block, exc)
            continue

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

        if tag.kind == RFID.CLASSIC and _deep_read_enabled:
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
