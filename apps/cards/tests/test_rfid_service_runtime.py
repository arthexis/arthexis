from __future__ import annotations

from apps.cards import rfid_service
from apps.cards import reader
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
