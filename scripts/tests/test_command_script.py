"""Regression tests for the shared command wrapper contract."""

import subprocess
from pathlib import Path

import pytest

from utils import command_api

COMMAND_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "command.sh"
COMMAND_BATCH_PATH = Path(__file__).resolve().parents[2] / "command.bat"
EXPECTED_LIST_USAGE = "Usage: arthexis cmd list [--deprecated] [--celery|--no-celery]"
EXPECTED_RUN_USAGE = "Usage: arthexis cmd run [--deprecated] [--celery|--no-celery] <django-command> [args...]"


@pytest.fixture(scope="module")
def command_script_contents() -> str:
    """Load command.sh source once for static wrapper assertions."""
    return COMMAND_SCRIPT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def command_batch_contents() -> str:
    """Load command.bat source once for static wrapper assertions."""
    return COMMAND_BATCH_PATH.read_text(encoding="utf-8")


def test_run_manage_raises_command_api_error_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Timeouts from manage.py should be reported as CommandApiError."""

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

    monkeypatch.setattr(command_api.subprocess, "run", timeout)
    with pytest.raises(command_api.CommandApiError, match="timed out"):
        command_api._run_manage(tmp_path, "help", "--commands")


def test_absorbed_command_discovery_checks_class_dict() -> None:
    """Absorbed discovery should not treat subclasses as absorbed by inheritance."""

    assert (
        'cls.__dict__.get("arthexis_absorbed_command", False)'
        in command_api.ABSORBED_COMMAND_DISCOVERY_SCRIPT
    )
