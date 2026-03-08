from __future__ import annotations

from apps.screens.lcd_screen import hardware


class _FakeNode:
    def __init__(self, *, has_lcd: bool, has_rtc: bool) -> None:
        self._has_lcd = has_lcd
        self._has_rtc = has_rtc

    def has_feature(self, slug: str) -> bool:
        if slug == "lcd-screen":
            return self._has_lcd
        if slug == "gpio-rtc":
            return self._has_rtc
        return False


def test_initialize_lcd_skips_when_node_has_no_capability(monkeypatch):
    monkeypatch.setattr("apps.nodes.models.Node.get_local", lambda: _FakeNode(has_lcd=False, has_rtc=False))
    monkeypatch.setattr(
        hardware,
        "prepare_lcd_controller",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not initialize")),
    )

    assert hardware._initialize_lcd() is None


def test_initialize_lcd_uses_prepare_when_node_capability_assigned(monkeypatch):
    calls: list[bool] = []

    monkeypatch.setattr("apps.nodes.models.Node.get_local", lambda: _FakeNode(has_lcd=True, has_rtc=False))

    def fake_prepare(*, diagnostics: bool = False, **_kwargs):
        calls.append(diagnostics)
        return object()

    monkeypatch.setattr(hardware, "prepare_lcd_controller", fake_prepare)

    lcd = hardware._initialize_lcd(diagnostics=True)

    assert lcd is not None
    assert calls == [True]
