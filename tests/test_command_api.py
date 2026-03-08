"""Regression tests for the command API wrapper."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from utils import command_api


def test_run_command_forwards_no_celery_flag(monkeypatch):
    """Regression: run-command execution should preserve the selected celery flag."""

    captured: dict[str, list[str] | Path] = {}

    monkeypatch.setenv("ARTHEXIS_COMMAND_FAST_RUN", "1")

    def fake_run(command, cwd, check, **kwargs):  # noqa: ANN001, ARG001
        captured["command"] = command
        captured["cwd"] = cwd
        captured["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(command_api.subprocess, "run", fake_run)

    result = command_api.run_command(
        Path("/tmp/arthexis"),
        "https",
        ["--godaddy", "example.com"],
        command_api.CommandOptions(celery=False),
    )

    assert result == 0
    assert captured["command"] == [
        command_api.sys.executable,
        "manage.py",
        "--no-celery",
        "https",
        "--godaddy",
        "example.com",
    ]


def test_run_command_forwards_celery_flag(monkeypatch):
    """run-command execution should preserve explicit celery mode requests."""

    captured: dict[str, list[str]] = {}

    monkeypatch.setenv("ARTHEXIS_COMMAND_FAST_RUN", "1")

    def fake_run(command, cwd, check, **kwargs):  # noqa: ANN001, ARG001
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(command_api.subprocess, "run", fake_run)

    result = command_api.run_command(
        Path("/tmp/arthexis"),
        "summary",
        [],
        command_api.CommandOptions(celery=True),
    )

    assert result == 0
    assert "--celery" in captured["command"]
