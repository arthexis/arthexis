import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
from django.conf import settings
from django.core.management import call_command


pytestmark = pytest.mark.integration


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
