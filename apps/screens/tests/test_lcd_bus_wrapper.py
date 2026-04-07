from types import SimpleNamespace

import pytest

from apps.screens.lcd import LCDUnavailableError, _BusWrapper


class _RaisingBus:
    def __init__(self, _channel: int) -> None:
        raise FileNotFoundError("/dev/i2c-1")


class _PermissionDeniedBus:
    def __init__(self, _channel: int) -> None:
        raise PermissionError("denied")


class _WriteErrorBus:
    def __init__(self, _channel: int) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def write_byte(self, _addr: int, _data: int) -> None:
        raise OSError("remote i/o error")


def test_bus_wrapper_raises_lcd_unavailable_when_i2c_device_missing(monkeypatch):
    monkeypatch.setattr("apps.screens.lcd.smbus", SimpleNamespace(SMBus=_RaisingBus))

    wrapper = _BusWrapper(channel=1)

    with pytest.raises(LCDUnavailableError) as exc:
        wrapper.write_byte(0x27, 0x00)

    assert "I2C bus device for channel 1 is unavailable" in str(exc.value)
    assert isinstance(exc.value.__cause__, FileNotFoundError)


def test_bus_wrapper_raises_lcd_unavailable_when_i2c_bus_access_denied(monkeypatch):
    monkeypatch.setattr(
        "apps.screens.lcd.smbus", SimpleNamespace(SMBus=_PermissionDeniedBus)
    )

    wrapper = _BusWrapper(channel=1)

    with pytest.raises(LCDUnavailableError) as exc:
        wrapper.write_byte(0x27, 0x00)

    assert "I2C bus device for channel 1 is unavailable" in str(exc.value)
    assert isinstance(exc.value.__cause__, PermissionError)


def test_bus_wrapper_closes_bus_when_write_byte_raises(monkeypatch):
    bus = _WriteErrorBus(1)
    monkeypatch.setattr("apps.screens.lcd.smbus", SimpleNamespace(SMBus=lambda _: bus))

    wrapper = _BusWrapper(channel=1)

    with pytest.raises(OSError):
        wrapper.write_byte(0x27, 0x00)

    assert bus.closed is True
