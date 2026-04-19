from __future__ import annotations

import json
import subprocess

import pytest
from django.core.management.base import CommandError

from apps.tests.management.commands import test as test_command


def test_run_pytest_prints_readiness_before_execution(monkeypatch, tmp_path, capsys):
    command = test_command.Command()
    fake_venv_python = test_command.expected_venv_python(tmp_path)
    fake_venv_python.parent.mkdir(parents=True)
    fake_venv_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(command, "_base_dir", lambda: tmp_path)
    monkeypatch.setattr(test_command, "resolve_project_python", lambda _base_dir: "python")

    probe_payload = {
        "python_executable": "/workspace/arthexis/.venv/bin/python",
        "virtualenv_active": True,
        "virtualenv_path": "/workspace/arthexis/.venv",
        "dependencies": {
            "pytest": True,
            "pytest-django": True,
            "pytest-timeout": True,
        },
    }

    calls: list[list[str]] = []

    def fake_run(command_args, **kwargs):
        calls.append(command_args)
        if command_args[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(command_args, 0, stdout=json.dumps(probe_payload))
        return subprocess.CompletedProcess(command_args, 0)

    monkeypatch.setattr(test_command.subprocess, "run", fake_run)

    command._run_pytest(["--", "apps/tests/test_test_command.py"])

    captured = capsys.readouterr().out
    assert "QA readiness:" in captured
    assert "virtualenv active: yes" in captured
    assert "python executable: /workspace/arthexis/.venv/bin/python" in captured
    assert "core test dependencies: pytest=yes" in captured

    assert calls[0][:2] == ["python", "-c"]
    assert calls[1] == ["python", "-m", "pytest", "apps/tests/test_test_command.py"]


def test_run_pytest_fails_before_pytest_when_dependency_missing(monkeypatch, tmp_path):
    command = test_command.Command()
    fake_venv_python = test_command.expected_venv_python(tmp_path)
    fake_venv_python.parent.mkdir(parents=True)
    fake_venv_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(command, "_base_dir", lambda: tmp_path)
    monkeypatch.setattr(test_command, "resolve_project_python", lambda _base_dir: "python")

    probe_payload = {
        "python_executable": "/workspace/arthexis/.venv/bin/python",
        "virtualenv_active": True,
        "virtualenv_path": "/workspace/arthexis/.venv",
        "dependencies": {
            "pytest": True,
            "pytest-django": False,
            "pytest-timeout": True,
        },
    }

    calls: list[list[str]] = []

    def fake_run(command_args, **kwargs):
        calls.append(command_args)
        return subprocess.CompletedProcess(command_args, 0, stdout=json.dumps(probe_payload))

    monkeypatch.setattr(test_command.subprocess, "run", fake_run)

    with pytest.raises(CommandError) as exc:
        command._run_pytest(["--", "apps/tests/test_test_command.py"])

    payload = json.loads(str(exc.value))
    assert payload == {
        "code": "missing_dependency",
        "command": "./env-refresh.sh --deps-only",
        "event": "arthexis.qa.remediation",
        "retry": ".venv/bin/python manage.py test run -- apps/tests/test_test_command.py",
    }
    assert len(calls) == 1
