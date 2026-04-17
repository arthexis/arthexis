from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.management.base import CommandError

from apps.tests.management.commands.migrations import Command as MigrationsCommand
from apps.tests.management.commands.test import Command as TestCommand
from utils.qa_remediation import expected_venv_python


def test_test_command_emits_bootstrap_remediation_when_venv_python_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    command = TestCommand()
    monkeypatch.setattr(command, "_base_dir", lambda: tmp_path)
    retry_command = f"{expected_venv_python(tmp_path).relative_to(tmp_path).as_posix()} manage.py test run"
    with pytest.raises(CommandError) as excinfo:
        command._run_pytest([])

    payload = json.loads(str(excinfo.value))
    assert payload == {
        "code": "missing_venv_python",
        "command": "./install.sh --terminal",
        "event": "arthexis.qa.remediation",
        "retry": retry_command,
    }


def test_test_command_emits_dependency_refresh_remediation(
    monkeypatch, tmp_path: Path
) -> None:
    command = TestCommand()
    fake_venv_python = expected_venv_python(tmp_path)
    fake_venv_python.parent.mkdir(parents=True)
    fake_venv_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(command, "_base_dir", lambda: tmp_path)

    class _ProbeResult:
        returncode = 1

    monkeypatch.setattr(
        "apps.tests.management.commands.test.subprocess.run",
        lambda *args, **kwargs: _ProbeResult(),
    )

    with pytest.raises(CommandError) as excinfo:
        command._run_pytest(
            ["--", "-k", "smoke and not slow", "apps/core/tests/test_doctor_command.py"]
        )

    payload = json.loads(str(excinfo.value))
    retry_prefix = expected_venv_python(tmp_path).relative_to(tmp_path).as_posix()
    assert payload == {
        "code": "missing_dependency",
        "command": "./env-refresh.sh --deps-only",
        "event": "arthexis.qa.remediation",
        "retry": (
            f"{retry_prefix} manage.py test run -- -k 'smoke and not slow' "
            "apps/core/tests/test_doctor_command.py"
        ),
    }


def test_migrations_command_emits_bootstrap_remediation_when_venv_python_missing(
    monkeypatch, tmp_path: Path
) -> None:
    command = MigrationsCommand()
    monkeypatch.setattr(command, "_base_dir", lambda: tmp_path)
    retry_command = (
        f"{expected_venv_python(tmp_path).relative_to(tmp_path).as_posix()} "
        "manage.py migrations check"
    )

    with pytest.raises(CommandError) as excinfo:
        command.handle(
            action="check",
            database="default",
            app_label=None,
            migration_name=None,
        )

    payload = json.loads(str(excinfo.value))
    assert payload == {
        "code": "missing_venv_python",
        "command": "./install.sh --terminal",
        "event": "arthexis.qa.remediation",
        "retry": retry_command,
    }


def test_migrations_retry_command_preserves_requested_options(
    monkeypatch, tmp_path: Path
) -> None:
    command = MigrationsCommand()
    monkeypatch.setattr(command, "_base_dir", lambda: tmp_path)

    retry = command._retry_command_for_options(
        {
            "action": "run",
            "app_label": "billing",
            "migration_name": "0012_auto",
            "database": "reports replica",
        }
    )

    retry_prefix = expected_venv_python(tmp_path).relative_to(tmp_path).as_posix()
    assert retry == (
        f"{retry_prefix} manage.py migrations run billing 0012_auto "
        "--database 'reports replica'"
    )
