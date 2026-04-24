from __future__ import annotations

import logging

from apps.cards import background_reader


def test_setup_hardware_gpio_missing_disables_reader(caplog, monkeypatch):
    monkeypatch.setattr(background_reader, "GPIO", None)
    monkeypatch.setattr(background_reader, "_hardware_disabled_reason", None)

    with caplog.at_level(logging.INFO):
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
    assert "WARNING" not in caplog.text


def test_record_setup_failure_logs_info_for_expected_missing_hardware(caplog, monkeypatch):
    monkeypatch.setattr(
        background_reader,
        "_hardware_disabled_reason",
        "GPIO library not available",
    )
    monkeypatch.setattr(background_reader, "_last_setup_failure", None)

    with caplog.at_level(logging.INFO):
        background_reader._record_setup_failure("initialization")

    assert "RFID hardware setup failed" in caplog.text
    assert "WARNING" not in caplog.text


def test_record_setup_failure_uses_explicit_detail_without_global_reason(
    caplog,
    monkeypatch,
):
    monkeypatch.setattr(background_reader, "_hardware_disabled_reason", None)
    monkeypatch.setattr(background_reader, "_last_setup_failure", None)

    with caplog.at_level(logging.INFO):
        background_reader._record_setup_failure(
            "initialization",
            "GPIO library not available",
        )

    assert "RFID hardware setup failed" in caplog.text
    assert "WARNING" not in caplog.text


def test_record_setup_failure_logs_warning_for_unexpected_failures(caplog, monkeypatch):
    monkeypatch.setattr(background_reader, "_hardware_disabled_reason", None)
    monkeypatch.setattr(background_reader, "_last_setup_failure", None)

    with caplog.at_level(logging.WARNING):
        background_reader._record_setup_failure("initialization")

    assert "RFID hardware setup failed" in caplog.text


def test_start_skips_when_hardware_is_disabled(monkeypatch):
    monkeypatch.setattr(background_reader, "_hardware_disabled_reason", "missing gpio")
    monkeypatch.setattr(background_reader, "_thread", None)

    def _unexpected_thread(*_args, **_kwargs):
        raise AssertionError("background thread should not start when hardware disabled")

    monkeypatch.setattr(background_reader.threading, "Thread", _unexpected_thread)

    background_reader.start()


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


def test_get_next_tag_polls_with_full_timeout_when_irq_queue_is_empty(monkeypatch):
    captured: dict[str, float] = {}

    class EmptyQueue:
        def get(self, *, timeout):
            captured["queue_timeout"] = timeout
            raise background_reader.queue.Empty

    monkeypatch.setattr(background_reader, "is_configured", lambda: True)
    monkeypatch.setattr(background_reader, "_tag_queue", EmptyQueue())
    monkeypatch.setattr(background_reader, "_log_fd_snapshot", lambda label: None)
    monkeypatch.setattr(background_reader._irq_empty_tracker, "record", lambda: None)
    monkeypatch.setattr(
        background_reader._irq_empty_tracker,
        "log_summary",
        lambda event: None,
    )
    monkeypatch.setattr(background_reader, "_mark_scanner_used", lambda: None)

    from apps.cards import reader

    def read_rfid(*, mfrc, cleanup, timeout):
        captured["poll_timeout"] = timeout
        return {"rfid": "ABCD1234", "label_id": 7}

    monkeypatch.setattr(reader, "read_rfid", read_rfid)

    result = background_reader.get_next_tag(timeout=0.2)

    assert result == {"rfid": "ABCD1234", "label_id": 7}
    assert captured == {"queue_timeout": 0.2, "poll_timeout": 0.2}
