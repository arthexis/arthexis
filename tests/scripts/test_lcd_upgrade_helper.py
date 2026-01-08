from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_helper_module():
    helper_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "helpers"
        / "lcd-upgrade-helper.py"
    )
    spec = importlib.util.spec_from_file_location("lcd_upgrade_helper", helper_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_init_lcd_resets_after_init(monkeypatch):
    module = _load_helper_module()

    class FakeLCD:
        def __init__(self):
            self.calls = []

        def init_lcd(self):
            self.calls.append("init_lcd")

        def reset(self):
            self.calls.append("reset")

    import apps.screens.lcd as lcd_module

    monkeypatch.setattr(lcd_module, "CharLCD1602", FakeLCD)

    lcd = module._init_lcd()

    assert isinstance(lcd, FakeLCD)
    assert lcd.calls == ["init_lcd", "reset"]
