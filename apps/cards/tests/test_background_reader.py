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
    assert [
        message
        for message in caplog.messages
        if "RFID hardware disabled for this process after setup failure" in message
    ] == [
        "RFID hardware disabled for this process after setup failure: "
        "GPIO library not available"
    ]


def test_start_skips_when_hardware_is_disabled(monkeypatch):
    monkeypatch.setattr(background_reader, "_hardware_disabled_reason", "missing gpio")
    monkeypatch.setattr(background_reader, "_thread", None)

    def _unexpected_thread(*_args, **_kwargs):
        raise AssertionError("background thread should not start when hardware disabled")

    monkeypatch.setattr(background_reader.threading, "Thread", _unexpected_thread)

    background_reader.start()
