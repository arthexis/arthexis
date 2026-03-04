"""Tests for command execution behavior in the canonical command API."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from utils import command_api


class _FakeResult(SimpleNamespace):
    """Simple subprocess result replacement used by tests."""



def test_run_command_passes_celery_flag(monkeypatch: object) -> None:
    """`run_command` should forward `--celery` to manage.py invocations."""

    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], cwd: Path, check: bool) -> _FakeResult:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["check"] = check
        return _FakeResult(returncode=0)

    monkeypatch.setattr(command_api.subprocess, "run", _fake_run)

    result = command_api.run_command(
        base_dir=Path("/tmp/project"),
        raw_command="feature",
        command_args=["--kind", "suite"],
        options=command_api.CommandOptions(celery=True, deprecated=False),
    )

    assert result == 0
    assert captured["cmd"] == [
        command_api.sys.executable,
        "manage.py",
        "--celery",
        "feature",
        "--kind",
        "suite",
    ]
    assert captured["check"] is False



def test_run_command_passes_no_celery_flag(monkeypatch: object) -> None:
    """`run_command` should forward `--no-celery` when celery mode is disabled."""

    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], cwd: Path, check: bool) -> _FakeResult:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["check"] = check
        return _FakeResult(returncode=0)

    monkeypatch.setattr(command_api.subprocess, "run", _fake_run)

    result = command_api.run_command(
        base_dir=Path("/tmp/project"),
        raw_command="chargers",
        command_args=[],
        options=command_api.CommandOptions(celery=False, deprecated=False),
    )

    assert result == 0
    assert captured["cmd"] == [
        command_api.sys.executable,
        "manage.py",
        "--no-celery",
        "chargers",
    ]
    assert captured["check"] is False
