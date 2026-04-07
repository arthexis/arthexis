from types import SimpleNamespace

import pytest

from apps.screens.lcd import LCDUnavailableError, _BusWrapper


class _RaisingBus:
    def __init__(self, _channel: int) -> None:
        raise FileNotFoundError("/dev/i2c-1")


def test_bus_wrapper_raises_lcd_unavailable_when_i2c_device_missing(monkeypatch):
    monkeypatch.setattr("apps.screens.lcd.smbus", SimpleNamespace(SMBus=_RaisingBus))

    wrapper = _BusWrapper(channel=1)

    with pytest.raises(LCDUnavailableError) as exc:
        wrapper.write_byte(0x27, 0x00)

    assert "I2C bus device for channel 1 is unavailable" in str(exc.value)
    assert isinstance(exc.value.__cause__, FileNotFoundError)
