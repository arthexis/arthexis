import os
import re
import subprocess
import time
from django.utils import timezone
from django.core.exceptions import ValidationError

from core.models import RFID
from core.notifications import notify_async

from .constants import (
    DEFAULT_RST_PIN,
    GPIO_PIN_MODE_BCM,
    SPI_BUS,
    SPI_DEVICE,
)


_deep_read_enabled: bool = False

_HEX_RE = re.compile(r"^[0-9A-F]+$")


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
    if command:
        env = os.environ.copy()
        env["RFID_VALUE"] = rfid
        env["RFID_LABEL_ID"] = str(tag.pk)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        except Exception:
            command_allowed = False
        else:
            command_allowed = completed.returncode == 0
        allowed = allowed and command_allowed

    result = {
        "rfid": rfid,
        "label_id": tag.pk,
        "created": created,
        "color": tag.color,
        "allowed": allowed,
        "released": tag.released,
        "reference": tag.reference.value if tag.reference else None,
        "kind": tag.kind,
    }
    status_text = "OK" if allowed else "BAD"
    color_word = (tag.color or "").upper()
    notify_async(f"RFID {tag.label_id} {status_text}".strip(), f"{rfid} {color_word}".strip())
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
        return {"error": str(exc)}

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
                    defaults = {"kind": kind}
                    tag, created = RFID.objects.get_or_create(
                        rfid=rfid, defaults=defaults
                    )
                    result = _build_tag_response(
                        tag,
                        rfid,
                        created=created,
                        kind=kind,
                    )
                    deep_read_active = tag.kind == RFID.CLASSIC and _deep_read_enabled
                    if deep_read_active:
                        keys = {}
                        if hasattr(tag, "key_a"):
                            keys["a"] = getattr(tag, "key_a") or ""
                            keys["a_verified"] = bool(
                                getattr(tag, "key_a_verified", False)
                            )
                        if hasattr(tag, "key_b"):
                            keys["b"] = getattr(tag, "key_b") or ""
                            keys["b_verified"] = bool(
                                getattr(tag, "key_b_verified", False)
                            )
                        if keys:
                            result["keys"] = keys
                        result["deep_read"] = True
                        dump = []
                        default_key = [0xFF] * 6
                        for block in range(64):
                            try:
                                used_key = None
                                status = mfrc.MFRC522_Auth(
                                    mfrc.PICC_AUTHENT1A, block, default_key, uid
                                )
                                if status == mfrc.MI_OK:
                                    used_key = "A"
                                if status != mfrc.MI_OK:
                                    status = mfrc.MFRC522_Auth(
                                        mfrc.PICC_AUTHENT1B, block, default_key, uid
                                    )
                                    if status == mfrc.MI_OK:
                                        used_key = "B"
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
                            except Exception:
                                continue
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
        return {"error": str(exc)}
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


def validate_rfid_value(value: object, *, kind: str | None = None) -> dict:
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

    defaults = {"kind": normalized_kind} if normalized_kind else {}

    try:
        tag, created = RFID.objects.get_or_create(
            rfid=normalized, defaults=defaults
        )
    except ValidationError as exc:
        return {"error": "; ".join(exc.messages)}
    except Exception as exc:  # pragma: no cover - defensive fallback
        return {"error": str(exc)}

    return _build_tag_response(tag, normalized, created=created, kind=normalized_kind)
