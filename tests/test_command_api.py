"""Tests for validated command API subprocess construction."""

from __future__ import annotations

from pathlib import Path

from utils import command_api


class FakeCompletedProcess:
    """Completed process stub for command API subprocess tests."""

    def __init__(
        self, returncode: int = 0, stdout: str = "ok", stderr: str = ""
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_manage_uses_validated_project_python(monkeypatch, tmp_path: Path) -> None:
    """Manage invocations should prefer the repository virtualenv interpreter."""

    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeCompletedProcess(stdout="commands")

    monkeypatch.setattr(
        command_api,
        "resolve_project_python",
        lambda base_dir: "/tmp/project/.venv/bin/python",
    )
    monkeypatch.setattr(command_api.subprocess, "run", fake_run)

    output = command_api._run_manage(tmp_path, "help", "--commands")

    assert output == "commands"
    assert captured["cmd"] == [
        "/tmp/project/.venv/bin/python",
        "manage.py",
        "help",
        "--commands",
    ]
    assert captured["kwargs"]["cwd"] == tmp_path


def test_run_command_uses_validated_project_python(monkeypatch, tmp_path: Path) -> None:
    """Validated command execution should use the repository virtualenv interpreter."""

    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(
        command_api,
        "resolve_project_python",
        lambda base_dir: "/tmp/project/.venv/bin/python",
    )
    monkeypatch.setattr(
        command_api,
        "_resolve_command",
        lambda base_dir, raw_command, options: "check",
    )
    monkeypatch.setattr(command_api.subprocess, "run", fake_run)

    exit_code = command_api.run_command(
        tmp_path,
        "check",
        ["--deploy"],
        command_api.CommandOptions(celery=False, deprecated=False),
    )

    assert exit_code == 0
    assert captured["cmd"] == [
        "/tmp/project/.venv/bin/python",
        "manage.py",
        "--no-celery",
        "check",
        "--deploy",
    ]
    assert captured["kwargs"]["cwd"] == tmp_path
