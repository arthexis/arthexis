"""RC522 RFID reader interface."""

try:  # pragma: no cover - hardware-specific
    import RPi.GPIO as GPIO  # type: ignore
    from mfrc522 import SimpleMFRC522
except Exception:  # pragma: no cover - missing on non-Pi systems
    GPIO = None
    SimpleMFRC522 = None


class RC522Reader:  # pragma: no cover - requires hardware
    """Simple wrapper around :class:`~mfrc522.SimpleMFRC522`."""

    def __init__(self):
        if SimpleMFRC522 is None:
            raise RuntimeError("mfrc522 library not available")
        self._reader = SimpleMFRC522()

    def read(self):
        """Read an RFID tag and return ``(id, text)``."""
        try:
            return self._reader.read()
        finally:
            if GPIO is not None:
                GPIO.cleanup()
