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
