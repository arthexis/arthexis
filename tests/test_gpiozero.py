import pytest
from gpiozero import Device
from gpiozero.pins.mock import MockFactory

from nodes.gpiozero import LEDController


def test_led_controller_toggle():
    Device.pin_factory = None
    led = LEDController(17)
    assert isinstance(Device.pin_factory, MockFactory)
    assert not led.is_lit
    led.on()
    assert led.is_lit
    led.off()
    assert not led.is_lit
    led.close()


def test_led_controller_missing_library(monkeypatch):
    monkeypatch.setattr('nodes.gpiozero.LED', None)
    with pytest.raises(RuntimeError):
        LEDController(1)
