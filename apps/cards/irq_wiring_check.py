import errno
from unittest import TestCase
from unittest.mock import patch, MagicMock, ANY

from .background_reader import _setup_hardware, IRQ_PIN, GPIO
from .reader import read_rfid


class IRQPinSetupManualTest(TestCase):
    """Manual test to ensure IRQ pin setup uses the expected GPIO pin."""

    def test_irq_pin_setup(self):
        with (
            patch("apps.cards.background_reader.GPIO") as mock_gpio,
            patch.dict("sys.modules", {"mfrc522": MagicMock(MFRC522=MagicMock())}),
        ):
            _setup_hardware()
            mock_gpio.setmode.assert_called_once_with(mock_gpio.BCM)
            mock_gpio.setup.assert_called_once_with(
                IRQ_PIN, mock_gpio.IN, pull_up_down=mock_gpio.PUD_UP
            )
            mock_gpio.add_event_detect.assert_called_once_with(
                IRQ_PIN, mock_gpio.FALLING, callback=ANY
            )


def _is_resource_busy(message: str | None, err_no: int | None) -> bool:
    """Return ``True`` when ``message``/``err_no`` indicates a busy device."""

    if err_no in {errno.EBUSY, errno.EAGAIN}:
        return True

    if not message:
        return False

    normalized = message.lower()
    return any(
        phrase in normalized
        for phrase in (
            "device or resource busy",
            "resource busy",
            "device busy",
            "resource temporarily unavailable",
        )
    )


def check_irq_pin():
    """Return the IRQ pin used by the reader or report if none is detected."""
    if _setup_hardware():
        if GPIO:
            try:  # pragma: no cover - hardware cleanup
                GPIO.remove_event_detect(IRQ_PIN)
                GPIO.cleanup()
            except Exception:
                pass
        return {"irq_pin": IRQ_PIN}

    result = read_rfid(timeout=0.1)
    error_message = result.get("error")
    errno_value = result.get("errno")
    if error_message:
        if _is_resource_busy(error_message, errno_value):
            response = {"irq_pin": None, "busy": True}
            reason = error_message.strip() if isinstance(error_message, str) else error_message
            if reason:
                response["reason"] = reason
            if errno_value is not None:
                response["errno"] = errno_value
            return response
        return {"error": "no scanner detected"}
    return {"irq_pin": None}
