from __future__ import annotations

import json
import queue
from types import SimpleNamespace

from apps.cards import background_reader, reader, rfid_service, scanner


class _RecordingLock:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.held = False

    def __enter__(self):
        self.events.append("enter")
        self.held = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.held = False
        self.events.append("exit")
        return False


def test_rfid_service_module_main_invokes_runner(monkeypatch):
    """Module entrypoint should run the RFID service without manage.py."""

    monkeypatch.setattr("sys.argv", ["python", "--host", "0.0.0.0", "--port", "29999"])
    captured: dict[str, object] = {}
    call_order: list[str] = []

    def fake_run_service(*, host: str | None = None, port: int | None = None) -> None:
        call_order.append("run_service")
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(rfid_service, "loadenv", lambda: call_order.append("loadenv"))
    monkeypatch.setattr(
        rfid_service,
        "bootstrap_sqlite_driver",
        lambda: call_order.append("bootstrap_sqlite_driver"),
    )
    monkeypatch.setattr(
        rfid_service.django,
        "setup",
        lambda: call_order.append("django.setup"),
    )
    monkeypatch.setattr(rfid_service, "run_service", fake_run_service)

    rfid_service.main()

    assert captured == {"host": "0.0.0.0", "port": 29999}
    assert call_order == [
        "loadenv",
        "bootstrap_sqlite_driver",
        "django.setup",
        "run_service",
    ]


def test_scan_sources_falls_back_to_attempts_for_lockfile_ingest_error(monkeypatch):
    """Scanner command should poll ingested attempts when service is lock-file based."""

    sentinel = {"rfid": "ABCD1234", "service_mode": "service"}
    monkeypatch.setattr(
        scanner,
        "request_service",
        lambda action, payload=None, timeout=0.7: {
            "error": "scan requests are handled via lock-file ingest"
        },
    )
    monkeypatch.setattr(scanner, "_scan_from_attempts", lambda **kwargs: sentinel)

    result = scanner.scan_sources(timeout=0.5)

    assert result == sentinel


def test_write_rfid_scan_lock_persists_latest_scan_state(settings, tmp_path):
    settings.BASE_DIR = tmp_path

    rfid_service.write_rfid_scan_lock(
        {"rfid": "abcd1234", "label_id": 7, "custom_label": "Front Desk"}
    )
    path = rfid_service.rfid_scan_lock_path(tmp_path)

    first = json.loads(path.read_text(encoding="utf-8"))
    assert first["schema"] == rfid_service.SCAN_STATE_SCHEMA
    assert first["rfid"] == "ABCD1234"
    assert first["label_id"] == 7
    assert first["custom_label"] == "Front Desk"
    assert first["scanned_at"]

    rfid_service.write_rfid_scan_lock({"rfid": "feed01", "label_id": 8})

    latest = json.loads(path.read_text(encoding="utf-8"))
    assert latest["rfid"] == "FEED01"
    assert latest["label_id"] == 8
    assert "custom_label" not in latest
    assert not any(entry.name.endswith(".tmp") for entry in path.parent.iterdir())


def test_write_rfid_scan_lock_uses_uuid_temp_name(settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    captured: list[str] = []
    original_write_text = rfid_service.Path.write_text

    monkeypatch.setattr(
        rfid_service.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="fixeduuid"),
    )

    def capture_write_text(self, *args, **kwargs):
        captured.append(self.name)
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(rfid_service.Path, "write_text", capture_write_text)

    rfid_service.write_rfid_scan_lock({"rfid": "abcd1234"})

    assert captured == [
        f".{rfid_service.SCAN_STATE_FILE}.{rfid_service.os.getpid()}.fixeduuid.tmp"
    ]


def test_default_auto_initialize_unknown_is_opt_in(monkeypatch):
    monkeypatch.delenv("RFID_SERVICE_AUTO_INITIALIZE_UNKNOWN", raising=False)
    assert rfid_service.default_auto_initialize_unknown() is False

    monkeypatch.setenv("RFID_SERVICE_AUTO_INITIALIZE_UNKNOWN", "true")
    assert rfid_service.default_auto_initialize_unknown() is True


def test_emit_scan_artifacts_uses_shared_payload_timestamp(settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    appended: list[dict[str, object]] = []
    timestamp = "2026-04-24T22:00:00+00:00"

    monkeypatch.setattr(rfid_service, "utc_now_iso", lambda: timestamp)
    monkeypatch.setattr(
        rfid_service,
        "append_scan_log",
        lambda payload: appended.append(
            rfid_service.normalize_scan_log_payload(payload)
        ),
    )

    state = rfid_service.RFIDServiceState()
    state._emit_scan_artifacts({"rfid": "abcd1234", "label_id": 7})

    lock_payload = json.loads(
        rfid_service.rfid_scan_lock_path(tmp_path).read_text(encoding="utf-8")
    )
    assert lock_payload["scanned_at"] == timestamp
    assert appended[0]["scanned_at"] == timestamp
    assert appended[0]["schema"] == rfid_service.SCAN_STATE_SCHEMA


def test_format_lcd_scan_event_prefers_card_label():
    result = rfid_service.format_lcd_scan_event(
        {"label_id": 42, "custom_label": "Front Desk", "rfid": "abcd1234"}
    )

    assert result == ("Label Front Desk", "ID ABCD1234")


def test_format_lcd_scan_event_prefers_card_lcd_label():
    result = rfid_service.format_lcd_scan_event(
        {
            "label_id": 42,
            "custom_label": "Front Desk",
            "lcd_label": "Door Online\nTap card",
            "rfid": "abcd1234",
        }
    )

    assert result == ("Door Online", "Tap card")


def test_rfid_service_scan_notifies_lcd_for_ten_seconds(settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    sent: list[dict[str, object]] = []

    monkeypatch.setattr(rfid_service, "lcd_feature_enabled", lambda lock_dir: True)
    monkeypatch.setattr(
        rfid_service,
        "notify_event_async",
        lambda subject, body, **kwargs: sent.append(
            {"subject": subject, "body": body, **kwargs}
        ),
    )

    state = rfid_service.RFIDServiceState()
    state._notify_lcd_event({"label_id": 7, "rfid": "cafe01"})

    assert sent == [
        {
            "subject": "Label 7",
            "body": "ID CAFE01",
            "duration": 10,
            "event_id": 0,
        }
    ]


def test_rfid_service_scan_extends_lcd_when_same_card_stays_present(
    settings,
    tmp_path,
    monkeypatch,
):
    settings.BASE_DIR = tmp_path
    sent: list[dict[str, object]] = []
    ticks = iter([10.0, 14.9, 15.1])

    monkeypatch.setattr(rfid_service, "lcd_feature_enabled", lambda lock_dir: True)
    monkeypatch.setattr(rfid_service.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        rfid_service,
        "notify_event_async",
        lambda subject, body, **kwargs: sent.append(
            {"subject": subject, "body": body, **kwargs}
        ),
    )

    state = rfid_service.RFIDServiceState()
    payload = {"label_id": 7, "rfid": "cafe01"}
    state._notify_lcd_event(payload)
    state._notify_lcd_event(payload)
    state._notify_lcd_event(payload)

    assert sent == [
        {
            "subject": "Label 7",
            "body": "ID CAFE01",
            "duration": 10,
            "event_id": 0,
        },
        {
            "subject": "Label 7",
            "body": "ID CAFE01",
            "duration": 10,
            "event_id": 0,
        },
    ]


def test_rfid_service_scan_notifies_lcd_immediately_for_different_card(
    settings,
    tmp_path,
    monkeypatch,
):
    settings.BASE_DIR = tmp_path
    sent: list[tuple[str, str]] = []
    ticks = iter([10.0, 11.0])

    monkeypatch.setattr(rfid_service, "lcd_feature_enabled", lambda lock_dir: True)
    monkeypatch.setattr(rfid_service.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        rfid_service,
        "notify_event_async",
        lambda subject, body, **kwargs: sent.append((subject, body)),
    )

    state = rfid_service.RFIDServiceState()
    state._notify_lcd_event({"label_id": 7, "rfid": "cafe01"})
    state._notify_lcd_event({"label_id": 8, "rfid": "beef02"})

    assert sent == [("Label 7", "ID CAFE01"), ("Label 8", "ID BEEF02")]


def test_rfid_service_scan_skips_lcd_when_feature_disabled(settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    sent: list[object] = []

    monkeypatch.setattr(rfid_service, "lcd_feature_enabled", lambda lock_dir: False)
    monkeypatch.setattr(
        rfid_service,
        "notify_event_async",
        lambda *args, **kwargs: sent.append(args),
    )

    state = rfid_service.RFIDServiceState()
    state._notify_lcd_event({"label_id": 7, "rfid": "cafe01"})

    assert sent == []


def test_rfid_service_scan_skips_lcd_without_rfid(settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    sent: list[object] = []

    monkeypatch.setattr(rfid_service, "lcd_feature_enabled", lambda lock_dir: True)
    monkeypatch.setattr(
        rfid_service,
        "notify_event_async",
        lambda *args, **kwargs: sent.append(args),
    )

    state = rfid_service.RFIDServiceState()
    state._notify_lcd_event({"label_id": 7})

    assert sent == []


def test_scan_sources_no_irq_uses_direct_reader(monkeypatch):
    """`--no-irq` scans should bypass the service and poll the reader directly."""

    sentinel = {"rfid": "ABCD1234", "service_mode": "on-demand"}
    monkeypatch.setattr(
        scanner,
        "request_service",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("service should not be called")
        ),
    )
    monkeypatch.setattr(
        reader,
        "read_rfid",
        lambda **kwargs: {"rfid": "ABCD1234", "label_id": 7},
    )
    monkeypatch.setattr(
        scanner,
        "record_scan_attempt",
        lambda result, **kwargs: object(),
    )
    monkeypatch.setattr(
        scanner,
        "build_attempt_response",
        lambda attempt, **kwargs: sentinel,
    )

    result = scanner.scan_sources(timeout=0.5, no_irq=True)

    assert result == sentinel


def test_scan_sources_no_irq_returns_direct_empty_result(monkeypatch):
    """Empty direct reads should return without recording an attempt."""

    monkeypatch.setattr(
        scanner,
        "request_service",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("service should not be called")
        ),
    )
    monkeypatch.setattr(
        reader,
        "read_rfid",
        lambda **kwargs: {"rfid": None, "label_id": None},
    )
    monkeypatch.setattr(
        scanner,
        "record_scan_attempt",
        lambda result, **kwargs: (_ for _ in ()).throw(
            AssertionError("empty scan should not be recorded")
        ),
    )

    result = scanner.scan_sources(timeout=0.5, no_irq=True)

    assert result == {"rfid": None, "label_id": None}


def _read_latest_scan_lock(base_dir):
    lock_path = base_dir / ".locks" / "rfid-scan.json"
    return json.loads(lock_path.read_text(encoding="utf-8"))


def _read_scan_log_entries(base_dir):
    log_path = base_dir / "logs" / "rfid-scans.ndjson"
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _zero_managed_dump():
    return [
        {"block": block, "data": [0] * 16}
        for sector in rfid_service.managed_sector_numbers()
        for block in rfid_service.sector_data_blocks(sector)
    ]


def test_rfid_service_auto_deep_scans_held_card_and_preserves_enrichment(
    monkeypatch,
    settings,
    tmp_path,
):
    """Holding the same card should enrich the latest-scan lockfile once."""

    settings.BASE_DIR = str(tmp_path)
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path / "logs"), raising=False)
    clock = {"now": 100.0}
    deep_calls: list[float] = []

    monkeypatch.setattr(rfid_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rfid_service, "default_scan_dedupe_seconds", lambda: 0.0)
    monkeypatch.setattr(rfid_service, "default_deep_scan_hold_seconds", lambda: 2.0)
    monkeypatch.setattr(rfid_service, "default_deep_scan_timeout", lambda: 0.1)
    monkeypatch.setattr(rfid_service, "default_auto_initialize_unknown", lambda: False)

    def fake_read_deep_tag(timeout):
        deep_calls.append(timeout)
        return {
            "rfid": "abcd1234",
            "label_id": "alpha",
            "deep_read": True,
            "keys": {"a": "FFFFFFFFFFFF", "a_verified": True},
            "dump": [{"block": 0, "data": [1, 2, 3]}],
        }

    monkeypatch.setattr(rfid_service, "read_deep_tag", fake_read_deep_tag)

    state = rfid_service.RFIDServiceState()
    state._emit_scan_artifacts({"rfid": "abcd1234", "label_id": "alpha"})
    first_payload = _read_latest_scan_lock(tmp_path)

    assert first_payload["schema"] == rfid_service.SCAN_LOCK_SCHEMA
    assert first_payload["rfid"] == "ABCD1234"
    assert first_payload["presence_duration_seconds"] == 0.0
    assert "dump" not in first_payload
    assert deep_calls == []

    clock["now"] = 101.5
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})
    assert deep_calls == []

    clock["now"] = 102.1
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})
    enriched_payload = _read_latest_scan_lock(tmp_path)

    assert deep_calls == [0.1]
    assert enriched_payload["deep_read"] is True
    assert enriched_payload["keys"] == {"a": "FFFFFFFFFFFF", "a_verified": True}
    assert enriched_payload["dump"] == [{"block": 0, "data": [1, 2, 3]}]
    assert enriched_payload["deep_scan"]["automatic"] is True
    assert enriched_payload["deep_scan"]["status"] == "ok"
    assert enriched_payload["presence_duration_seconds"] == 2.1
    assert enriched_payload["last_presence_at"]
    enriched_log_payload = _read_scan_log_entries(tmp_path)[-1]
    assert enriched_log_payload["deep_scan"]["status"] == "ok"
    assert "keys" not in enriched_log_payload
    assert "dump" not in enriched_log_payload

    clock["now"] = 103.5
    state._emit_scan_artifacts(
        {"rfid": "ABCD1234", "label_id": "alpha", "allowed": True}
    )
    retained_payload = _read_latest_scan_lock(tmp_path)

    assert deep_calls == [0.1]
    assert retained_payload["dump"] == [{"block": 0, "data": [1, 2, 3]}]
    assert retained_payload["allowed"] is True
    assert retained_payload["presence_duration_seconds"] == 3.5

    clock["now"] = 104.6
    state._emit_scan_artifacts({"rfid": "BEEF0001", "label_id": "beta"})
    different_card_payload = _read_latest_scan_lock(tmp_path)

    assert different_card_payload["rfid"] == "BEEF0001"
    assert different_card_payload["presence_duration_seconds"] == 0.0
    assert "dump" not in different_card_payload


def test_rfid_service_resets_presence_when_same_card_returns_after_gap(
    monkeypatch,
    settings,
    tmp_path,
):
    settings.BASE_DIR = str(tmp_path)
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path / "logs"), raising=False)
    clock = {"now": 100.0}
    deep_calls: list[float] = []

    monkeypatch.setattr(rfid_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rfid_service, "default_scan_dedupe_seconds", lambda: 0.0)
    monkeypatch.setattr(rfid_service, "default_deep_scan_hold_seconds", lambda: 2.0)
    monkeypatch.setattr(rfid_service, "default_deep_scan_timeout", lambda: 0.1)
    monkeypatch.setattr(rfid_service, "default_presence_gap_seconds", lambda: 2.0)
    monkeypatch.setattr(rfid_service, "default_auto_initialize_unknown", lambda: False)

    def fake_read_deep_tag(timeout):
        deep_calls.append(timeout)
        return {
            "rfid": "abcd1234",
            "deep_read": True,
            "dump": [{"block": 0, "data": [1, 2, 3]}],
        }

    monkeypatch.setattr(rfid_service, "read_deep_tag", fake_read_deep_tag)

    state = rfid_service.RFIDServiceState()
    state._emit_scan_artifacts({"rfid": "abcd1234", "label_id": "alpha"})

    clock["now"] = 101.0
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})

    clock["now"] = 104.5
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})
    returned_payload = _read_latest_scan_lock(tmp_path)

    assert returned_payload["presence_duration_seconds"] == 0.0
    assert "dump" not in returned_payload
    assert deep_calls == []

    clock["now"] = 105.5
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})
    held_again_payload = _read_latest_scan_lock(tmp_path)

    assert held_again_payload["presence_duration_seconds"] == 1.0
    assert "dump" not in held_again_payload

    clock["now"] = 106.6
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})
    enriched_payload = _read_latest_scan_lock(tmp_path)

    assert deep_calls == [0.1]
    assert enriched_payload["presence_duration_seconds"] == 2.1
    assert enriched_payload["deep_scan"]["status"] == "ok"


def test_rfid_service_dedupes_repeated_deep_read_payloads(
    monkeypatch,
    settings,
    tmp_path,
):
    settings.BASE_DIR = str(tmp_path)
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path / "logs"), raising=False)
    clock = {"now": 100.0}
    deep_payload = {
        "rfid": "abcd1234",
        "label_id": "alpha",
        "deep_read": True,
        "dump": [{"block": 0, "data": [1, 2, 3]}],
    }

    monkeypatch.setattr(rfid_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rfid_service, "default_scan_dedupe_seconds", lambda: 1.0)

    state = rfid_service.RFIDServiceState()
    state._emit_scan_artifacts({"rfid": "abcd1234", "label_id": "alpha"})
    assert len(_read_scan_log_entries(tmp_path)) == 1

    clock["now"] = 100.1
    state._emit_scan_artifacts(deep_payload)
    assert len(_read_scan_log_entries(tmp_path)) == 2

    clock["now"] = 100.2
    state._emit_scan_artifacts(deep_payload)
    assert len(_read_scan_log_entries(tmp_path)) == 2

    clock["now"] = 101.2
    state._emit_scan_artifacts(deep_payload)
    assert len(_read_scan_log_entries(tmp_path)) == 3


def test_rfid_service_auto_initializes_unformatted_held_classic_card(
    monkeypatch,
    settings,
    tmp_path,
):
    settings.BASE_DIR = str(tmp_path)
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path / "logs"), raising=False)
    clock = {"now": 100.0}
    init_calls: list[float] = []

    monkeypatch.setattr(rfid_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rfid_service, "default_scan_dedupe_seconds", lambda: 0.0)
    monkeypatch.setattr(rfid_service, "default_deep_scan_hold_seconds", lambda: 2.0)
    monkeypatch.setattr(rfid_service, "default_deep_scan_timeout", lambda: 0.1)
    monkeypatch.setattr(rfid_service, "default_auto_initialize_unknown", lambda: True)

    def fake_read_deep_tag(timeout):
        return {
            "rfid": "abcd1234",
            "kind": "CLASSIC",
            "initialized": False,
            "deep_read": True,
            "dump": _zero_managed_dump(),
        }

    def fake_initialize_current_tag(timeout):
        init_calls.append(timeout)
        return {"rfid": "ABCD1234", "initialized": True}

    monkeypatch.setattr(rfid_service, "read_deep_tag", fake_read_deep_tag)
    monkeypatch.setattr(rfid_service, "initialize_current_tag", fake_initialize_current_tag)

    state = rfid_service.RFIDServiceState()
    state._emit_scan_artifacts({"rfid": "abcd1234", "label_id": "alpha"})

    clock["now"] = 101.0
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})

    clock["now"] = 102.1
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})
    enriched_payload = _read_latest_scan_lock(tmp_path)

    assert init_calls == [0.1]
    assert enriched_payload["initialization"]["status"] == "ok"
    assert enriched_payload["initialization"]["automatic"] is True


def test_should_auto_initialize_unknown_requires_complete_managed_dump(monkeypatch):
    monkeypatch.setattr(rfid_service, "default_auto_initialize_unknown", lambda: True)

    partial_payload = {
        "kind": "CLASSIC",
        "initialized": False,
        "dump": [{"block": 12, "data": [0] * 16}],
    }
    complete_payload = {
        "kind": "CLASSIC",
        "initialized": False,
        "dump": _zero_managed_dump(),
    }

    assert rfid_service.should_auto_initialize_unknown(partial_payload) is False
    assert rfid_service.should_auto_initialize_unknown(complete_payload) is True


def test_normalize_initialization_payload_reports_partial_failure():
    payload = {
        "rfid": "ABCD1234",
        "initialized": False,
        "errors": [{"sector": 4, "errors": ["block 16"]}],
    }

    result = rfid_service.normalize_initialization_payload(
        payload,
        attempted_at="2026-05-13T23:00:00+00:00",
    )

    assert result["status"] == "failed"
    assert result["automatic"] is True
    assert result["errors"] == [{"sector": 4, "errors": ["block 16"]}]


def test_rfid_service_rejects_auto_initialization_rfid_mismatch(
    monkeypatch,
    settings,
    tmp_path,
):
    settings.BASE_DIR = str(tmp_path)
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path / "logs"), raising=False)
    clock = {"now": 100.0}

    monkeypatch.setattr(rfid_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rfid_service, "default_scan_dedupe_seconds", lambda: 0.0)
    monkeypatch.setattr(rfid_service, "default_deep_scan_hold_seconds", lambda: 2.0)
    monkeypatch.setattr(rfid_service, "default_deep_scan_timeout", lambda: 0.1)
    monkeypatch.setattr(rfid_service, "default_auto_initialize_unknown", lambda: True)

    def fake_read_deep_tag(timeout):
        return {
            "rfid": "ABCD1234",
            "kind": "CLASSIC",
            "initialized": False,
            "deep_read": True,
            "dump": _zero_managed_dump(),
        }

    monkeypatch.setattr(rfid_service, "read_deep_tag", fake_read_deep_tag)
    monkeypatch.setattr(
        rfid_service,
        "initialize_current_tag",
        lambda timeout: {"rfid": "DEADBEEF", "initialized": True},
    )

    state = rfid_service.RFIDServiceState()
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})

    clock["now"] = 101.0
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})

    clock["now"] = 102.1
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})
    enriched_payload = _read_latest_scan_lock(tmp_path)

    assert enriched_payload["initialization"] == {
        "automatic": True,
        "attempted_at": enriched_payload["scanned_at"],
        "status": "rfid-mismatch",
        "rfid": "DEADBEEF",
    }


def test_rfid_service_preserves_initialization_error_without_rfid(
    monkeypatch,
    settings,
    tmp_path,
):
    settings.BASE_DIR = str(tmp_path)
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path / "logs"), raising=False)
    clock = {"now": 100.0}

    monkeypatch.setattr(rfid_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rfid_service, "default_scan_dedupe_seconds", lambda: 0.0)
    monkeypatch.setattr(rfid_service, "default_deep_scan_hold_seconds", lambda: 2.0)
    monkeypatch.setattr(rfid_service, "default_deep_scan_timeout", lambda: 0.1)
    monkeypatch.setattr(rfid_service, "default_auto_initialize_unknown", lambda: True)

    def fake_read_deep_tag(timeout):
        return {
            "rfid": "ABCD1234",
            "kind": "CLASSIC",
            "initialized": False,
            "deep_read": True,
            "dump": _zero_managed_dump(),
        }

    monkeypatch.setattr(rfid_service, "read_deep_tag", fake_read_deep_tag)
    monkeypatch.setattr(
        rfid_service,
        "initialize_current_tag",
        lambda timeout: {"error": "reader unavailable", "errno": 5},
    )

    state = rfid_service.RFIDServiceState()
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})

    clock["now"] = 101.0
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})

    clock["now"] = 102.1
    state._emit_scan_artifacts({"rfid": "ABCD1234", "label_id": "alpha"})
    enriched_payload = _read_latest_scan_lock(tmp_path)

    assert enriched_payload["initialization"] == {
        "automatic": True,
        "attempted_at": enriched_payload["scanned_at"],
        "error": "reader unavailable",
        "errno": 5,
        "status": "error",
    }


def test_background_reader_deep_read_uses_direct_reader_and_restores_state(
    monkeypatch,
):
    """Automatic deep reads should not drain a stale fast-read queue entry."""

    sentinel_reader = object()
    reader_lock = _RecordingLock()
    deep_state = {"enabled": False}
    read_calls = []
    usage_marks = []

    monkeypatch.setattr(background_reader, "is_configured", lambda: True)
    monkeypatch.setattr(background_reader, "_reader", sentinel_reader)
    monkeypatch.setattr(background_reader, "_reader_lock", reader_lock)
    monkeypatch.setattr(reader, "deep_read_enabled", lambda: deep_state["enabled"])

    def fake_enable_deep_read():
        deep_state["enabled"] = True
        return True

    def fake_disable_deep_read():
        deep_state["enabled"] = False
        return False

    def fake_read_rfid(*, mfrc=None, cleanup=True, timeout=1.0, **kwargs):
        read_calls.append(
            {
                "mfrc": mfrc,
                "cleanup": cleanup,
                "timeout": timeout,
                "deep_enabled": deep_state["enabled"],
                "lock_held": reader_lock.held,
            }
        )
        return {"rfid": "ABCD1234"}

    monkeypatch.setattr(reader, "enable_deep_read", fake_enable_deep_read)
    monkeypatch.setattr(reader, "disable_deep_read", fake_disable_deep_read)
    monkeypatch.setattr(reader, "read_rfid", fake_read_rfid)
    monkeypatch.setattr(
        background_reader,
        "_mark_scanner_used",
        lambda: usage_marks.append("used"),
    )

    result = background_reader.read_current_tag_deep(timeout=0.25)

    assert result == {"rfid": "ABCD1234"}
    assert read_calls == [
        {
            "mfrc": sentinel_reader,
            "cleanup": False,
            "timeout": 0.25,
            "deep_enabled": True,
            "lock_held": True,
        }
    ]
    assert deep_state["enabled"] is False
    assert usage_marks == ["used"]
    assert reader_lock.events == ["enter", "exit"]


def test_background_reader_polling_read_uses_reader_lock(monkeypatch):
    sentinel_reader = object()
    reader_lock = _RecordingLock()
    read_calls = []
    usage_marks = []

    monkeypatch.setattr(background_reader, "is_configured", lambda: True)
    monkeypatch.setattr(background_reader, "_reader", sentinel_reader)
    monkeypatch.setattr(background_reader, "_reader_lock", reader_lock)
    monkeypatch.setattr(background_reader, "_tag_queue", queue.Queue())
    monkeypatch.setattr(
        background_reader,
        "_mark_scanner_used",
        lambda: usage_marks.append("used"),
    )

    def fake_read_rfid(*, mfrc=None, cleanup=True, timeout=1.0, **kwargs):
        read_calls.append(
            {
                "mfrc": mfrc,
                "cleanup": cleanup,
                "timeout": timeout,
                "lock_held": reader_lock.held,
            }
        )
        return {"rfid": "ABCD1234"}

    monkeypatch.setattr(reader, "read_rfid", fake_read_rfid)

    result = background_reader.get_next_tag(timeout=0.0)

    assert result == {"rfid": "ABCD1234"}
    assert read_calls == [
        {
            "mfrc": sentinel_reader,
            "cleanup": False,
            "timeout": 0.0,
            "lock_held": True,
        }
    ]
    assert usage_marks == ["used"]
    assert reader_lock.events == ["enter", "exit"]


def test_background_reader_irq_callback_uses_reader_lock(monkeypatch):
    reader_lock = _RecordingLock()
    read_calls = []

    class DummyReader:
        ComIrqReg = 0x04

        def __init__(self) -> None:
            self.writes: list[tuple[int, int, bool]] = []

        def dev_write(self, register, value):
            self.writes.append((register, value, reader_lock.held))

    dummy_reader = DummyReader()
    tag_queue = queue.Queue()

    monkeypatch.setattr(background_reader, "_reader", dummy_reader)
    monkeypatch.setattr(background_reader, "_reader_lock", reader_lock)
    monkeypatch.setattr(background_reader, "_tag_queue", tag_queue)

    def fake_read_rfid(*, mfrc=None, cleanup=True, use_irq=False, **kwargs):
        read_calls.append(
            {
                "mfrc": mfrc,
                "cleanup": cleanup,
                "use_irq": use_irq,
                "lock_held": reader_lock.held,
            }
        )
        return {"rfid": "ABCD1234"}

    monkeypatch.setattr(reader, "read_rfid", fake_read_rfid)

    background_reader._irq_callback(22)

    assert tag_queue.get_nowait() == {"rfid": "ABCD1234"}
    assert read_calls == [
        {
            "mfrc": dummy_reader,
            "cleanup": False,
            "use_irq": True,
            "lock_held": True,
        }
    ]
    assert dummy_reader.writes == [(dummy_reader.ComIrqReg, 0x7F, True)]
    assert reader_lock.events == ["enter", "exit"]
