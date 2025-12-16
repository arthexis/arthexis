import os
import re
import subprocess
import time
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.cards.models import RFID
from apps.core.notifications import notify_async

from .constants import (
    DEFAULT_RST_PIN,
    GPIO_PIN_MODE_BCM,
    SPI_BUS,
    SPI_DEVICE,
)
from apps.video.rfid import queue_camera_snapshot
from .utils import convert_endianness_value, normalize_endianness


_deep_read_enabled: bool = False

_HEX_RE = re.compile(r"^[0-9A-F]+$")
_KEY_RE = re.compile(r"^[0-9A-F]{12}$")


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
    raw_command = getattr(tag, "external_command", "")
    if isinstance(raw_command, str):
        command = raw_command.strip()
    else:
        command = ""
    command_details: dict[str, object] | None = None
    command_allowed = True
    if command:
        command_details = {
            "stdout": "",
            "stderr": "",
            "returncode": None,
            "error": "",
        }
        env = os.environ.copy()
        env["RFID_VALUE"] = rfid
        env["RFID_LABEL_ID"] = str(tag.pk)
        env["RFID_ENDIANNESS"] = getattr(tag, "endianness", RFID.BIG_ENDIAN)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        except Exception as exc:
            command_allowed = False
            command_details["error"] = _normalize_command_text(str(exc))
        else:
            command_returncode = getattr(completed, "returncode", 1)
            command_allowed = command_returncode == 0
            command_details["returncode"] = command_returncode
            command_details["stdout"] = _normalize_command_text(
                getattr(completed, "stdout", "") or ""
            )
            command_details["stderr"] = _normalize_command_text(
                getattr(completed, "stderr", "") or ""
            )
        allowed = allowed and command_allowed

    post_command = getattr(tag, "post_auth_command", "")
    if allowed and isinstance(post_command, str):
        post = post_command.strip()
        if post:
            env = os.environ.copy()
            env["RFID_VALUE"] = rfid
            env["RFID_LABEL_ID"] = str(tag.pk)
            env["RFID_ENDIANNESS"] = getattr(tag, "endianness", RFID.BIG_ENDIAN)
            try:
                subprocess.Popen(
                    post,
                    shell=True,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
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
        command_details["stdout"] = _normalize_command_text(
            command_details.get("stdout", "")
        )
        command_details["stderr"] = _normalize_command_text(
            command_details.get("stderr", "")
        )
        command_details["error"] = _normalize_command_text(
            command_details.get("error", "")
        )
        result["command_output"] = command_details
    status_text = "OK" if allowed else "BAD"
    color_word = (tag.color or "").upper()
    notify_async(f"RFID {tag.label_id} {status_text}".strip(), f"{rfid} {color_word}".strip())
    queue_camera_snapshot(rfid, result)
    return result


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
    """
    try:
        if mfrc is None:
            from mfrc522 import MFRC522  # type: ignore

            mfrc = MFRC522(
                bus=SPI_BUS,
                device=SPI_DEVICE,
                pin_mode=GPIO_PIN_MODE_BCM,
                pin_rst=DEFAULT_RST_PIN,
            )
    except Exception as exc:  # pragma: no cover - hardware dependent
        payload = {"error": str(exc)}
        errno_value = getattr(exc, "errno", None)
        if errno_value is not None:
            payload["errno"] = errno_value
        return payload

    try:
        import RPi.GPIO as GPIO  # pragma: no cover - hardware dependent
    except Exception:  # pragma: no cover - hardware dependent
        GPIO = None

    try:
        end = time.time() + timeout
        selected = False
        while time.time() < end:  # pragma: no cover - hardware loop
            (status, _tag_type) = mfrc.MFRC522_Request(mfrc.PICC_REQIDL)
            if status == mfrc.MI_OK:
                (status, uid) = mfrc.MFRC522_Anticoll()
                if status == mfrc.MI_OK:
                    uid_bytes = uid or []
                    try:
                        if uid_bytes:
                            selected = bool(mfrc.MFRC522_SelectTag(uid_bytes))
                        else:
                            selected = False
                    except Exception:
                        selected = False
                    rfid = "".join(f"{x:02X}" for x in uid_bytes)
                    kind = RFID.NTAG215 if len(uid_bytes) > 5 else RFID.CLASSIC
                    tag, created = RFID.register_scan(rfid, kind=kind)
                    result = _build_tag_response(
                        tag,
                        rfid,
                        created=created,
                        kind=kind,
                    )
                    deep_read_active = tag.kind == RFID.CLASSIC and _deep_read_enabled
                    if deep_read_active:
                        keys: dict[str, object] = {}
                        if hasattr(tag, "key_a"):
                            key_a_value = _normalize_key(getattr(tag, "key_a", ""))
                            keys["a"] = key_a_value or (getattr(tag, "key_a", "") or "")
                            keys["a_verified"] = bool(
                                getattr(tag, "key_a_verified", False)
                            )
                        if hasattr(tag, "key_b"):
                            key_b_value = _normalize_key(getattr(tag, "key_b", ""))
                            keys["b"] = key_b_value or (getattr(tag, "key_b", "") or "")
                            keys["b_verified"] = bool(
                                getattr(tag, "key_b_verified", False)
                            )

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
                                    status = mfrc.MFRC522_Auth(
                                        mfrc.PICC_AUTHENT1A, block, key_bytes, uid
                                    )
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
                                        r, data = read_status
                                    else:
                                        r, data = (mfrc.MI_OK, read_status)
                                    if r == mfrc.MI_OK and data is not None:
                                        entry = {"block": block, "data": list(data)}
                                        if used_key:
                                            entry["key"] = used_key
                                        dump.append(entry)

                                        if used_key == "A" and used_value:
                                            if used_value != keys.get("a"):
                                                keys["a"] = used_value
                                            if not keys.get("a_verified"):
                                                keys["a_verified"] = True
                                            if not getattr(tag, "key_a_verified", False) or getattr(
                                                tag, "key_a", ""
                                            ).upper() != used_value:
                                                setattr(tag, "key_a", used_value)
                                                setattr(tag, "key_a_verified", True)
                                                pending_updates.update(
                                                    {"key_a", "key_a_verified"}
                                                )
                                            if used_bytes is not None:
                                                key_candidates["A"] = [(used_value, used_bytes)]

                                        if used_key == "B" and used_value:
                                            if used_value != keys.get("b"):
                                                keys["b"] = used_value
                                            if not keys.get("b_verified"):
                                                keys["b_verified"] = True
                                            if not getattr(tag, "key_b_verified", False) or getattr(
                                                tag, "key_b", ""
                                            ).upper() != used_value:
                                                setattr(tag, "key_b", used_value)
                                                setattr(tag, "key_b_verified", True)
                                                pending_updates.update(
                                                    {"key_b", "key_b_verified"}
                                                )
                                            if used_bytes is not None:
                                                key_candidates["B"] = [(used_value, used_bytes)]
                            except Exception:
                                continue

                        if pending_updates:
                            tag.save(update_fields=sorted(pending_updates))

                        result["dump"] = dump
                        if getattr(tag, "data", None) != dump:
                            tag.data = [dict(entry) for entry in dump]
                            tag.save(update_fields=["data"])
                    return result
            if not use_irq and poll_interval:
                time.sleep(poll_interval)
        return {"rfid": None, "label_id": None}
    except Exception as exc:  # pragma: no cover - hardware dependent
        if "rfid" in locals():
            notify_async(f"RFID {rfid}", "Read failed")
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
        if cleanup and GPIO:
            try:
                GPIO.cleanup()
            except Exception:
                pass


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
