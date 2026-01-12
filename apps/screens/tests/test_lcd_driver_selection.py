from __future__ import annotations

import pytest

from apps.screens import lcd


def test_resolve_driver_auto_prefers_pcf_for_3e_only():
    assert lcd._resolve_driver("auto", addresses=["3e"]) == "pcf8574"


def test_resolve_driver_auto_prefers_aip_when_multiple_addresses():
    assert lcd._resolve_driver("auto", addresses=["3e", "20"]) == "aip31068"


def test_resolve_driver_honors_explicit_preference():
    assert lcd._resolve_driver("aip31068", addresses=["27"]) == "aip31068"
    assert lcd._resolve_driver("pcf8574", addresses=["3e"]) == "pcf8574"
    assert lcd._resolve_driver("unknown", addresses=["3e"]) == "pcf8574"


def test_prepare_lcd_controller_falls_back_when_auto_driver_fails(monkeypatch):
    calls: list[str] = []

    class FakeLCD:
        def __init__(self, name: str, fail: bool) -> None:
            self.name = name
            self.fail = fail

        def init_lcd(self) -> None:
            calls.append(f"init:{self.name}")
            if self.fail:
                raise RuntimeError("init failed")

    def fake_scan() -> list[str]:
        return ["3e", "20"]

    def fake_create(*, preference: str | None = None, **_kwargs):
        calls.append(f"create:{preference}")
        if preference == "aip31068":
            return FakeLCD("aip", True)
        return FakeLCD("pcf", False)

    monkeypatch.setattr(lcd, "scan_i2c_addresses", fake_scan)
    monkeypatch.setattr(lcd, "create_lcd_controller", fake_create)

    controller = lcd.prepare_lcd_controller(preference="auto")

    assert isinstance(controller, FakeLCD)
    assert controller.name == "pcf"
    assert calls == ["create:aip31068", "init:aip", "create:pcf8574", "init:pcf"]


def test_prepare_lcd_controller_does_not_fallback_on_explicit_preference(monkeypatch):
    class FakeLCD:
        def init_lcd(self) -> None:
            raise RuntimeError("init failed")

    monkeypatch.setattr(lcd, "scan_i2c_addresses", lambda: ["3e"])
    monkeypatch.setattr(lcd, "create_lcd_controller", lambda **_kwargs: FakeLCD())

    with pytest.raises(RuntimeError, match="init failed"):
        lcd.prepare_lcd_controller(preference="pcf8574")
