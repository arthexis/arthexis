"""Regression tests for migration server line-bump retry behavior."""

from __future__ import annotations

import subprocess

from utils.devtools import migration_server


def _completed(*, returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess[str]:
    """Build a completed-process payload matching migration runner contracts."""

    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_run_migrations_retries_on_next_database_for_known_line_failure(monkeypatch):
    """Known legacy cleanup failures should retry once on the configured fallback database."""

    commands: list[list[str]] = []
    results = [
        _completed(
            returncode=1,
            stderr=(
                "This database never completed the historical game cleanup migration."
            ),
        ),
        _completed(returncode=0),
    ]

    def fake_run_command(command: list[str]):
        commands.append(command)
        return results.pop(0)

    monkeypatch.setattr(migration_server, "_run_command", fake_run_command)

    exit_code = migration_server.run_migrations(next_database="next_line")

    assert exit_code == 0
    assert len(commands) == 2
    assert commands[0][-2:] == ["manage.py", "migrate"]
    assert commands[1][-4:] == ["manage.py", "migrate", "--database", "next_line"]


def test_run_migrations_skips_line_bump_when_error_is_not_candidate(monkeypatch):
    """Unrelated migration failures should not trigger next-line retries."""

    commands: list[list[str]] = []

    def fake_run_command(command: list[str]):
        commands.append(command)
        return _completed(returncode=1, stderr="permission denied")

    monkeypatch.setattr(migration_server, "_run_command", fake_run_command)

    exit_code = migration_server.run_migrations(next_database="next_line")

    assert exit_code == 1
    assert len(commands) == 1
