from unittest import TestCase
from unittest.mock import patch, MagicMock, ANY

from rfid.background_reader import _setup_hardware, IRQ_PIN, GPIO


class IRQPinSetupManualTest(TestCase):
    """Manual test to ensure IRQ pin setup uses the expected GPIO pin."""

    def test_irq_pin_setup(self):
        with patch("rfid.background_reader.GPIO") as mock_gpio, \
             patch.dict("sys.modules", {"mfrc522": MagicMock(MFRC522=MagicMock())}):
            _setup_hardware()
            mock_gpio.setmode.assert_called_once_with(mock_gpio.BCM)
            mock_gpio.setup.assert_called_once_with(
                IRQ_PIN, mock_gpio.IN, pull_up_down=mock_gpio.PUD_UP
            )
            mock_gpio.add_event_detect.assert_called_once_with(
                IRQ_PIN, mock_gpio.FALLING, callback=ANY
            )


def check_irq_pin():
    """Return the IRQ pin used by the reader or report if none is detected."""
    if not _setup_hardware():
        return {"error": "no scanner detected"}

    if GPIO:
        try:  # pragma: no cover - hardware cleanup
            GPIO.remove_event_detect(IRQ_PIN)
            GPIO.cleanup()
        except Exception:
            pass

    return {"irq_pin": IRQ_PIN}
