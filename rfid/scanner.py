"""Helpers for reading from the local RFID scanner."""

import atexit
from typing import Optional

from .reader import read_rfid


try:  # pragma: no cover - hardware dependent
    from mfrc522 import MFRC522  # type: ignore
except Exception:  # pragma: no cover - hardware dependent
    MFRC522 = None  # type: ignore

try:  # pragma: no cover - hardware dependent
    import RPi.GPIO as GPIO  # type: ignore
except Exception:  # pragma: no cover - hardware dependent
    GPIO = None  # type: ignore


_reader: Optional[object] = None


def _get_reader():  # pragma: no cover - hardware dependent
    """Initialise and cache the hardware reader."""
    global _reader
    if _reader is None and MFRC522 is not None:
        try:
            _reader = MFRC522()
        except Exception:
            _reader = None
    return _reader


def _cleanup():  # pragma: no cover - hardware dependent
    if GPIO is not None:
        try:
            GPIO.cleanup()
        except Exception:
            pass


atexit.register(_cleanup)


def scan_sources():
    """Read the next RFID tag from the local scanner."""
    reader = _get_reader()
    result = read_rfid(mfrc=reader, cleanup=False)
    if result and result.get("rfid"):
        return result
    return {"rfid": None, "label_id": None}

