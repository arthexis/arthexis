"""Tests for the VS Code one-shot migration runner."""

from __future__ import annotations

from unittest import mock

from apps.vscode import migration_server


def test_build_migration_command_with_extra_args() -> None:
    """Ensure migration command includes passthrough arguments."""

    command = migration_server.build_migration_command(["--plan"])
    assert command[-2:] == ["migrate", "--plan"]


def test_main_strips_remainder_separator() -> None:
    """Regression: CLI should ignore argparse's ``--`` remainder separator."""

    with mock.patch.object(migration_server, "run_migrations", return_value=0) as runner:
        migration_server.main(["--", "--plan"])

    runner.assert_called_once_with(["--plan"])


def test_run_migrations_returns_subprocess_exit_code() -> None:
    """Run result should mirror the subprocess return code."""

    completed = mock.Mock(returncode=3)
    with mock.patch.object(migration_server.subprocess, "run", return_value=completed):
        assert migration_server.run_migrations([]) == 3
