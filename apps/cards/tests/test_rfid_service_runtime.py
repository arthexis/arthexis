from __future__ import annotations

import json
from types import SimpleNamespace

from apps.cards import rfid_service
from apps.cards import scanner


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


def test_emit_scan_artifacts_uses_shared_payload_timestamp(settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    appended: list[dict[str, object]] = []
    timestamp = "2026-04-24T22:00:00+00:00"
    original_build_scan_state_payload = rfid_service.build_scan_state_payload

    def build_with_timestamp(payload):
        state = original_build_scan_state_payload(payload)
        state["scanned_at"] = timestamp
        return state

    monkeypatch.setattr(rfid_service, "build_scan_state_payload", build_with_timestamp)
    monkeypatch.setattr(
        rfid_service,
        "append_scan_log",
        lambda payload: appended.append(dict(payload)),
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
