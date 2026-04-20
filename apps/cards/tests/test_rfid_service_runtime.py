from __future__ import annotations

from apps.cards import rfid_service
from apps.cards import scanner


def test_rfid_service_module_main_invokes_runner(monkeypatch):
    """Module entrypoint should run the RFID service without manage.py."""

    monkeypatch.setattr("sys.argv", ["python", "--host", "0.0.0.0", "--port", "29999"])
    captured: dict[str, object] = {}

    def fake_run_service(*, host: str | None = None, port: int | None = None) -> None:
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(rfid_service, "run_service", fake_run_service)

    rfid_service.main()

    assert captured == {"host": "0.0.0.0", "port": 29999}


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
