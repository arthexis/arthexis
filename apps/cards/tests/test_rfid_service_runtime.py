from __future__ import annotations

import json

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
