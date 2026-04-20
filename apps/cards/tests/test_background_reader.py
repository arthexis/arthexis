from __future__ import annotations

from apps.cards import background_reader


def test_setup_hardware_gpio_missing_disables_reader(caplog, monkeypatch):
    monkeypatch.setattr(background_reader, "GPIO", None)
    monkeypatch.setattr(background_reader, "_hardware_disabled_reason", None)

    with caplog.at_level("WARNING"):
        first = background_reader._setup_hardware()
        second = background_reader._setup_hardware()

    assert first is False
    assert second is False
    assert background_reader._hardware_disabled_reason == "GPIO library not available"
    matches = [
        message
        for message in caplog.messages
        if "RFID hardware disabled for this process after setup failure" in message
    ]
    assert len(matches) == 1
    assert "GPIO library not available" in matches[0]


def test_start_disables_hardware_when_gpio_unavailable(monkeypatch):
    monkeypatch.setattr(background_reader, "_hardware_disabled_reason", None)
    monkeypatch.setattr(background_reader, "_thread", None)
    monkeypatch.setattr(background_reader, "is_configured", lambda: True)

    def _missing_gpio():
        background_reader._disable_hardware("GPIO library not available")
        return False

    monkeypatch.setattr(background_reader, "_ensure_gpio_loaded", _missing_gpio)

    background_reader.start()

    assert background_reader._hardware_disabled_reason == "GPIO library not available"


def test_start_skips_when_hardware_is_disabled(monkeypatch):
    monkeypatch.setattr(background_reader, "_hardware_disabled_reason", "missing gpio")
    monkeypatch.setattr(background_reader, "_thread", None)
    monkeypatch.setattr(background_reader, "is_configured", lambda: True)
    monkeypatch.setattr(background_reader, "_ensure_gpio_loaded", lambda: True)

    def _unexpected_thread(*_args, **_kwargs):
        raise AssertionError("background thread should not start when hardware disabled")

    monkeypatch.setattr(background_reader.threading, "Thread", _unexpected_thread)
    background_reader.start()
