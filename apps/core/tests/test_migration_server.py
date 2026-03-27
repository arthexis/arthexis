"""Regression tests for migration server line-bump retry behavior."""

from __future__ import annotations

import subprocess

import pytest

from utils.devtools import migration_server


def _completed(*, returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess[str]:
    """Build a completed-process payload matching migration runner contracts."""

    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.mark.parametrize(
    ("extra_args", "expected_retry_args"),
    [
        pytest.param(
            None,
            ["--database", "next_line"],
            id="adds-database-when-missing",
        ),
        pytest.param(
            ["--database", "default", "--plan"],
            ["--plan", "--database", "next_line"],
            id="replaces-separate-database-flag",
        ),
        pytest.param(
            ["--plan", "--database=default"],
            ["--plan", "--database", "next_line"],
            id="replaces-assignment-database-flag",
        ),
        pytest.param(
            ["--database", "default", "--database=shadow", "--plan"],
            ["--plan", "--database", "next_line"],
            id="removes-multiple-database-flags",
        ),
        pytest.param(
            ["--database", "--plan"],
            ["--plan", "--database", "next_line"],
            id="keeps-following-flag-when-database-value-missing",
        ),
    ],
)
def test_run_migrations_retries_on_next_database_for_known_line_failure(
    monkeypatch,
    extra_args,
    expected_retry_args,
):
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

    exit_code = migration_server.run_migrations(
        extra_args=extra_args,
        next_database="next_line",
    )

    assert exit_code == 0
    assert len(commands) == 2
    assert commands[0][1:3] == ["manage.py", "migrate"]
    assert commands[1][1:3] == ["manage.py", "migrate"]
    assert commands[1][3:] == expected_retry_args


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


def test_run_migrations_skips_retry_when_next_database_matches_current(monkeypatch):
    """Retry should not run when the fallback alias matches the active database."""

    commands: list[list[str]] = []

    def fake_run_command(command: list[str]):
        commands.append(command)
        return _completed(
            returncode=1,
            stderr="This database never completed the historical game cleanup migration.",
        )

    monkeypatch.setattr(migration_server, "_run_command", fake_run_command)

    exit_code = migration_server.run_migrations(
        extra_args=["--database", "next_line"],
        next_database="next_line",
    )

    assert exit_code == 1
    assert len(commands) == 1
