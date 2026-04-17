from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.management.base import CommandError

from apps.tests.management.commands.migrations import Command as MigrationsCommand
from apps.tests.management.commands.test import Command as TestCommand


def test_test_command_emits_bootstrap_remediation_when_venv_python_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    command = TestCommand()
    monkeypatch.setattr(command, "_base_dir", lambda: tmp_path)
    with pytest.raises(CommandError) as excinfo:
        command._run_pytest([])

    payload = json.loads(str(excinfo.value))
    assert payload == {
        "code": "missing_venv_python",
        "command": "./install.sh --terminal",
        "event": "arthexis.qa.remediation",
        "retry": ".venv/bin/python manage.py test run",
    }


def test_test_command_emits_dependency_refresh_remediation(
    monkeypatch, tmp_path: Path
) -> None:
    command = TestCommand()
    fake_venv_python = tmp_path / ".venv" / "bin" / "python"
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
        command._run_pytest(["--", "apps/core/tests/test_doctor_command.py"])

    payload = json.loads(str(excinfo.value))
    assert payload == {
        "code": "missing_dependency",
        "command": "./env-refresh.sh --deps-only",
        "event": "arthexis.qa.remediation",
        "retry": ".venv/bin/python manage.py test run -- apps/core/tests/test_doctor_command.py",
    }


def test_migrations_command_emits_bootstrap_remediation_when_venv_python_missing(
    monkeypatch, tmp_path: Path
) -> None:
    command = MigrationsCommand()
    monkeypatch.setattr(command, "_base_dir", lambda: tmp_path)

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
        "retry": ".venv/bin/python manage.py migrations check",
    }
