import importlib
import sys
import types
import builtins
import pytest


pytestmark = [
    pytest.mark.role("Terminal"),
    pytest.mark.role("Control"),
    pytest.mark.feature("lcd-screen"),
]


def test_charlcd1602_falls_back_to_smbus2(monkeypatch):
    """CharLCD1602 uses smbus2 when smbus is unavailable."""
    # make importing smbus raise ImportError
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "smbus":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    fake_bus = types.SimpleNamespace(
        write_byte=lambda *a, **k: None, close=lambda: None
    )
    fake_smbus2 = types.SimpleNamespace(SMBus=lambda channel: fake_bus)
    monkeypatch.setitem(sys.modules, "smbus2", fake_smbus2)

    if "nodes.lcd" in sys.modules:
        del sys.modules["nodes.lcd"]
    lcd_module = importlib.import_module("nodes.lcd")

    lcd = lcd_module.CharLCD1602()
    assert isinstance(lcd, lcd_module.CharLCD1602)


def test_init_lcd_defaults_when_scan_unavailable(monkeypatch):
    """``init_lcd`` falls back to the default address when scanning fails."""

    # Provide a fake smbus implementation so the module can be imported.
    fake_bus = types.SimpleNamespace(
        write_byte=lambda *a, **k: None, close=lambda: None
    )
    fake_smbus = types.SimpleNamespace(SMBus=lambda channel: fake_bus)
    monkeypatch.setitem(sys.modules, "smbus", fake_smbus)

    # Ensure a fresh import of nodes.lcd using the fake smbus.
    if "nodes.lcd" in sys.modules:
        del sys.modules["nodes.lcd"]
    lcd_module = importlib.import_module("nodes.lcd")

    lcd = lcd_module.CharLCD1602(bus=fake_bus)
    # Simulate ``i2cdetect`` being unavailable.
    monkeypatch.setattr(lcd, "i2c_scan", lambda: [])

    lcd.init_lcd()

    assert lcd.LCD_ADDR == lcd.PCF8574_address


def test_error_message_advises_installation(monkeypatch):
    """LCDUnavailableError suggests installing I2C dependencies."""

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"smbus", "smbus2"}:
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    if "nodes.lcd" in sys.modules:
        del sys.modules["nodes.lcd"]
    lcd_module = importlib.import_module("nodes.lcd")

    with pytest.raises(lcd_module.LCDUnavailableError) as exc:
        lcd_module.CharLCD1602()

    msg = str(exc.value)
    assert "sudo apt-get install i2c-tools python3-smbus" in msg
    assert "pip install smbus2" in msg
