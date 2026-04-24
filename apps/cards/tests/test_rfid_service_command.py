import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
from django.conf import settings
from django.core.management import call_command


def _write_lock(path: Path, content: str = "") -> None:
    """Write lock fixture content for command tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def subprocess_result(*, stdout: str, returncode: int):
    """Return a subprocess.CompletedProcess-like object."""
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


@pytest.mark.django_db
def test_rfid_service_debug_stops_active_unit(tmp_path, capsys, monkeypatch):
    """`rfid service --debug` stops active systemd unit before run."""
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path, raising=False)
    _write_lock(tmp_path / ".locks" / "rfid-service.lck")
    _write_lock(tmp_path / ".locks" / "service.lck", "demo")
    rfid_command = importlib.import_module("apps.cards.management.commands.rfid")

    with (
        patch.object(
            rfid_command.subprocess,
            "run",
            side_effect=[
                subprocess_result(stdout="active\n", returncode=0),
                subprocess_result(stdout="", returncode=0),
            ],
        ) as run_mock,
        patch.object(rfid_command, "run_service", return_value=None),
    ):
        call_command("rfid", "service", debug=True)

    output = capsys.readouterr().out
    assert "Stopping rfid-demo.service" in output
    assert "Stopped rfid-demo.service" in output
    assert run_mock.call_args_list == [
        call(["systemctl", "is-active", "rfid-demo.service"], capture_output=True, text=True, check=False),
        call(["systemctl", "stop", "rfid-demo.service"], capture_output=True, text=True, check=False),
    ]


@pytest.mark.django_db
def test_rfid_service_debug_warns_when_unit_inactive(tmp_path, capsys, monkeypatch):
    """`rfid service --debug` warns when service lock exists but unit is inactive."""
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path, raising=False)
    _write_lock(tmp_path / ".locks" / "rfid-service.lck")
    _write_lock(tmp_path / ".locks" / "service.lck", "demo")
    rfid_command = importlib.import_module("apps.cards.management.commands.rfid")

    with (
        patch.object(
            rfid_command.subprocess,
            "run",
            return_value=subprocess_result(stdout="inactive\n", returncode=3),
        ),
        patch.object(rfid_command, "run_service", return_value=None),
    ):
        call_command("rfid", "service", debug=True)

    output = capsys.readouterr().out
    assert "rfid-demo.service is not active" in output


@pytest.mark.django_db
def test_rfid_without_action_shows_status(tmp_path, capsys, monkeypatch):
    """Regression: `rfid` without an action reports default status instead of erroring."""
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path, raising=False)
    _write_lock(tmp_path / ".locks" / "rfid-service.lck")
    rfid_command = importlib.import_module("apps.cards.management.commands.rfid")

    with patch.object(
        rfid_command.rfid_service,
        "request_service",
        return_value={"ok": True},
    ):
        call_command("rfid")

    output = capsys.readouterr().out
    assert "RFID Status" in output
    assert "Service endpoint:" in output
    assert "RFID reader configuration:" in output
    assert "RFID service state: reachable" in output


@pytest.mark.django_db
def test_rfid_status_does_not_delete_scanner_lock(tmp_path, capsys, monkeypatch):
    """Status checks must not mutate scanner lock state."""
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path, raising=False)
    _write_lock(tmp_path / ".locks" / "rfid-service.lck")
    scanner_lock = tmp_path / ".locks" / "rfid-scan.json"
    _write_lock(scanner_lock, "stale-marker")
    rfid_command = importlib.import_module("apps.cards.management.commands.rfid")

    with patch.object(
        rfid_command.rfid_service,
        "request_service",
        return_value={"ok": True},
    ):
        call_command("rfid")

    output = capsys.readouterr().out
    assert "RFID reader configuration: configured" in output
    assert scanner_lock.exists()


@pytest.mark.django_db
def test_rfid_scan_requires_feature(monkeypatch):
    """RFID scan should not probe scanner hardware when feature is inactive."""

    rfid_command = importlib.import_module("apps.cards.management.commands.rfid")
    dummy_node = SimpleNamespace()
    monkeypatch.setattr(rfid_command.Node, "get_local", lambda: dummy_node)
    monkeypatch.setattr(rfid_command, "is_feature_active_for_node", lambda *, node, slug: False)
    monkeypatch.setattr(rfid_command, "scan_sources", lambda **kwargs: (_ for _ in ()).throw(AssertionError("scan_sources should not run")))

    with pytest.raises(rfid_command.CommandError, match="rfid-scanner feature is not active"):
        call_command("rfid", "check", "--scan", "--no-irq")


@pytest.mark.django_db
def test_rfid_scan_no_irq_bypasses_attempt_polling(monkeypatch):
    """`rfid check --scan --no-irq` should use the direct scanner path."""

    rfid_command = importlib.import_module("apps.cards.management.commands.rfid")
    monkeypatch.setattr(rfid_command.Command, "_scanner_feature_available", lambda _self: True)
    monkeypatch.setattr(
        rfid_command.Command,
        "_scan_via_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("attempt polling should be bypassed")
        ),
    )
    monkeypatch.setattr(
        rfid_command,
        "scan_sources",
        lambda **kwargs: {"rfid": "ABCD1234", "no_irq": kwargs.get("no_irq")},
    )

    command = rfid_command.Command()
    result = command._scan({"timeout": 1.0, "no_irq": True})

    assert result == {"rfid": "ABCD1234", "no_irq": True}


@pytest.mark.django_db
def test_rfid_scan_no_irq_empty_result_bypasses_service_state(monkeypatch):
    """Direct polling should report timeout even when the service is unavailable."""

    rfid_command = importlib.import_module("apps.cards.management.commands.rfid")
    monkeypatch.setattr(
        rfid_command,
        "scan_sources",
        lambda **kwargs: {"rfid": None, "label_id": None, "no_irq": kwargs.get("no_irq")},
    )
    monkeypatch.setattr(rfid_command, "service_available", lambda: False)

    command = rfid_command.Command()
    result = command._scan({"timeout": 1.0, "no_irq": True})

    assert result == {"error": "No RFID detected before timeout"}


@pytest.mark.django_db
def test_rfid_scan_fallback_uses_remaining_timeout(monkeypatch):
    """Fallback polling should not restart the full scan timeout."""

    rfid_command = importlib.import_module("apps.cards.management.commands.rfid")
    monotonic_values = iter([10.0, 11.5])
    captured: dict[str, float] = {}
    command = rfid_command.Command()

    monkeypatch.setattr(rfid_command.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        command,
        "_scan_via_attempt",
        lambda timeout: {"rfid": None, "label_id": None},
    )

    def fake_scan_via_local(timeout):
        captured["timeout"] = timeout
        return {"rfid": None, "label_id": None}

    monkeypatch.setattr(command, "_scan_via_local", fake_scan_via_local)
    monkeypatch.setattr(rfid_command, "service_available", lambda: True)

    result = command._scan({"timeout": 2.0, "no_irq": False})

    assert result == {"error": "No RFID detected before timeout"}
    assert captured["timeout"] == pytest.approx(0.5)


@pytest.mark.django_db
def test_rfid_scan_fallback_skips_local_when_timeout_spent(monkeypatch):
    """Fallback polling should stop when service polling used the budget."""

    rfid_command = importlib.import_module("apps.cards.management.commands.rfid")
    monotonic_values = iter([10.0, 12.1])
    command = rfid_command.Command()

    monkeypatch.setattr(rfid_command.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        command,
        "_scan_via_attempt",
        lambda timeout: {"rfid": None, "label_id": None},
    )
    monkeypatch.setattr(
        command,
        "_scan_via_local",
        lambda timeout: (_ for _ in ()).throw(
            AssertionError("local fallback should not run")
        ),
    )
    monkeypatch.setattr(rfid_command, "service_available", lambda: True)

    result = command._scan({"timeout": 2.0, "no_irq": False})

    assert result == {"error": "No RFID detected before timeout"}
