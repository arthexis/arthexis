from __future__ import annotations

import logging
import queue
import sys
from types import SimpleNamespace

from apps.cards import background_reader, reader


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


def test_setup_hardware_logs_info_for_expected_missing_device(caplog, monkeypatch):
    class DummyGPIO:
        BCM = "BCM"
        IN = "IN"
        PUD_UP = "PUD_UP"

        @staticmethod
        def setwarnings(_enabled):
            return None

        @staticmethod
        def setmode(_mode):
            return None

        @staticmethod
        def setup(_pin, _direction, pull_up_down=None):
            return None

        @staticmethod
        def cleanup():
            return None

    monkeypatch.setattr(background_reader, "GPIO", DummyGPIO)
    monkeypatch.setattr(background_reader, "resolve_spi_bus_device", lambda: (0, 0))
    monkeypatch.setattr(background_reader, "_reader", None)
    monkeypatch.setattr(background_reader, "_hardware_disabled_reason", None)

    with caplog.at_level(logging.INFO):
        with monkeypatch.context() as patch_ctx:
            patch_ctx.setitem(sys.modules, "mfrc522", SimpleNamespace())

            def _mfrc_ctor(**_kwargs):
                raise FileNotFoundError("[Errno 2] No such file or directory: '/dev/spidev0.0'")

            sys.modules["mfrc522"].MFRC522 = _mfrc_ctor
            assert background_reader._setup_hardware() is False

    assert "RFID hardware disabled for this process after setup failure" in caplog.text
    reader_logs = [record for record in caplog.records if record.name == background_reader.__name__]
    assert any(
        record.levelno == logging.INFO
        and "RFID hardware disabled for this process after setup failure" in record.getMessage()
        for record in reader_logs
    )
    assert not any(record.levelno >= logging.WARNING for record in reader_logs)


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


def test_lock_file_active_keeps_existing_service_lock(tmp_path, settings):
    settings.BASE_DIR = str(tmp_path)
    lock = tmp_path / ".locks" / "rfid-service.lck"
    lock.parent.mkdir(parents=True)
    lock.write_text("other-process-marker", encoding="utf-8")

    active, path = background_reader.lock_file_active()

    assert active is True
    assert path == lock
    assert lock.read_text(encoding="utf-8") == "other-process-marker"


def test_worker_setup_failure_does_not_delete_service_lock(tmp_path, settings, monkeypatch):
    settings.BASE_DIR = str(tmp_path)
    lock = tmp_path / ".locks" / "rfid-service.lck"
    lock.parent.mkdir(parents=True)
    lock.write_text("configured", encoding="utf-8")
    monkeypatch.setattr(background_reader, "_setup_hardware", lambda: False)
    monkeypatch.setattr(background_reader, "_record_setup_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr(background_reader, "_log_fd_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(background_reader, "_thread", object())

    background_reader._worker()

    assert lock.exists()
    assert background_reader._thread is None


def test_get_next_tag_polling_fallback_uses_original_timeout(monkeypatch):
    calls: list[float] = []

    class EmptyQueue:
        def get(self, timeout=None):
            raise queue.Empty

    def fake_read_rfid(**kwargs):
        calls.append(kwargs["timeout"])
        return {"rfid": None, "label_id": None}

    monkeypatch.setattr(background_reader, "is_configured", lambda: True)
    monkeypatch.setattr(background_reader, "_tag_queue", EmptyQueue())
    monkeypatch.setattr(background_reader, "_log_fd_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(reader, "read_rfid", fake_read_rfid)
    monkeypatch.setattr(
        background_reader,
        "_irq_empty_tracker",
        SimpleNamespace(record=lambda: None, log_summary=lambda _event: None),
    )

    assert background_reader.get_next_tag(timeout=0.2) is None
    assert calls == [0.2]
