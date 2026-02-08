from pathlib import Path
from unittest.mock import call, patch

import pytest
from django.conf import settings
from django.core.management import call_command


pytestmark = pytest.mark.integration


def _write_lock(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.django_db
def test_rfid_service_debug_stops_active_unit(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path, raising=False)
    _write_lock(tmp_path / ".locks" / "rfid-service.lck")
    _write_lock(tmp_path / ".locks" / "service.lck", "demo")

    with (
        patch(
            "apps.cards.management.commands.rfid_service.subprocess.run",
            side_effect=[
                subprocess_result(stdout="active\n", returncode=0),
                subprocess_result(stdout="", returncode=0),
            ],
        ) as run_mock,
        patch(
            "apps.cards.management.commands.rfid_service.run_service",
            return_value=None,
        ),
    ):
        call_command("rfid_service", debug=True)

    output = capsys.readouterr().out
    assert "Stopping rfid-demo.service" in output
    assert "Stopped rfid-demo.service" in output
    assert run_mock.call_args_list == [
        call(
            ["systemctl", "is-active", "rfid-demo.service"],
            capture_output=True,
            text=True,
            check=False,
        ),
        call(
            ["systemctl", "stop", "rfid-demo.service"],
            capture_output=True,
            text=True,
            check=False,
        ),
    ]


@pytest.mark.django_db
def test_rfid_service_debug_warns_when_unit_inactive(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path, raising=False)
    _write_lock(tmp_path / ".locks" / "rfid-service.lck")
    _write_lock(tmp_path / ".locks" / "service.lck", "demo")

    with (
        patch(
            "apps.cards.management.commands.rfid_service.subprocess.run",
            return_value=subprocess_result(stdout="inactive\n", returncode=3),
        ),
        patch(
            "apps.cards.management.commands.rfid_service.run_service",
            return_value=None,
        ),
    ):
        call_command("rfid_service", debug=True)

    output = capsys.readouterr().out
    assert "rfid-demo.service is not active" in output


def subprocess_result(*, stdout: str, returncode: int):
    return type(
        "Result",
        (),
        {"stdout": stdout, "stderr": "", "returncode": returncode},
    )()
