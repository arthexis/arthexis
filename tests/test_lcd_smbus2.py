import importlib
import sys
import types
import builtins


def test_charlcd1602_falls_back_to_smbus2(monkeypatch):
    """CharLCD1602 uses smbus2 when smbus is unavailable."""
    # make importing smbus raise ImportError
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "smbus":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    fake_bus = types.SimpleNamespace(write_byte=lambda *a, **k: None, close=lambda: None)
    fake_smbus2 = types.SimpleNamespace(SMBus=lambda channel: fake_bus)
    monkeypatch.setitem(sys.modules, "smbus2", fake_smbus2)

    if "nodes.lcd" in sys.modules:
        del sys.modules["nodes.lcd"]
    lcd_module = importlib.import_module("nodes.lcd")

    lcd = lcd_module.CharLCD1602()
    assert isinstance(lcd, lcd_module.CharLCD1602)
