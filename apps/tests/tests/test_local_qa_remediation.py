from __future__ import annotations

import builtins
import json
import sys
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
        returncode = 0
        stdout = (
            '{"python_executable": ".venv/bin/python", "virtualenv_active": true, '
            '"virtualenv_path": ".venv", "dependencies": {"pytest": false, '
            '"pytest-django": false, "pytest-timeout": false}}\n'
        )

    monkeypatch.setattr(
        "apps.tests.management.commands.test.subprocess.run",
        lambda *args, **kwargs: _ProbeResult(),
    )

    with pytest.raises(CommandError) as excinfo:
        command._run_pytest(
            ["--", "-k", "smoke", "apps/core/tests/test_doctor_command.py"]
        )

    payload = json.loads(str(excinfo.value))
    retry_prefix = expected_venv_python(tmp_path).relative_to(tmp_path).as_posix()
    assert payload == {
        "code": "missing_dependency",
        "command": "./env-refresh.sh --deps-only",
        "event": "arthexis.qa.remediation",
        "retry": (
            f"{retry_prefix} manage.py test run -- -k smoke "
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


def test_test_server_emits_dependency_remediation_when_import_fails(
    monkeypatch, tmp_path: Path
) -> None:
    command = TestCommand()
    fake_venv_python = expected_venv_python(tmp_path)
    fake_venv_python.parent.mkdir(parents=True)
    fake_venv_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(command, "_base_dir", lambda: tmp_path)
    original_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "utils.devtools":
            raise ModuleNotFoundError("No module named 'utils.devtools'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    sys.modules.pop("utils.devtools", None)

    with pytest.raises(CommandError) as excinfo:
        command._run_test_server()

    payload = json.loads(str(excinfo.value))
    retry_prefix = expected_venv_python(tmp_path).relative_to(tmp_path).as_posix()
    assert payload == {
        "code": "missing_dependency",
        "command": "./env-refresh.sh --deps-only",
        "event": "arthexis.qa.remediation",
        "retry": f"{retry_prefix} manage.py test server",
    }
