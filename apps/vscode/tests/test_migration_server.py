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

    completed = mock.Mock(returncode=3, stdout="", stderr="")
    with (
        mock.patch.object(migration_server.sys, "platform", "linux"),
        mock.patch.object(migration_server.subprocess, "run", return_value=completed) as run,
    ):
        assert migration_server.run_migrations([]) == 3

    run.assert_called_once_with(
        [migration_server.sys.executable, "manage.py", "migrate"],
        cwd=migration_server.BASE_DIR,
        check=False,
        capture_output=True,
        text=True,
        start_new_session=True,
    )


def test_run_migrations_handles_keyboard_interrupt() -> None:
    """Regression: runner should map interrupt signals to a shell-friendly code."""

    with mock.patch.object(
        migration_server.subprocess,
        "run",
        side_effect=KeyboardInterrupt,
    ):
        assert migration_server.run_migrations([]) == 130


def test_run_migrations_uses_new_process_group_on_windows() -> None:
    """Windows launches should isolate Ctrl+C signals in a child process group."""

    completed = mock.Mock(returncode=0, stdout="", stderr="")
    with (
        mock.patch.object(migration_server.sys, "platform", "win32"),
        mock.patch.object(
            migration_server.subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0x00000200,
            create=True,
        ),
        mock.patch.object(migration_server.subprocess, "run", return_value=completed) as run,
    ):
        assert migration_server.run_migrations([]) == 0

    run.assert_called_once_with(
        [migration_server.sys.executable, "manage.py", "migrate"],
        cwd=migration_server.BASE_DIR,
        check=False,
        capture_output=True,
        text=True,
        creationflags=0x00000200,
    )


def test_run_migrations_starts_new_session_on_posix() -> None:
    """POSIX launches should isolate Ctrl+C signals in a new session."""

    completed = mock.Mock(returncode=0, stdout="", stderr="")
    with (
        mock.patch.object(migration_server.sys, "platform", "linux"),
        mock.patch.object(migration_server.subprocess, "run", return_value=completed) as run,
    ):
        assert migration_server.run_migrations([]) == 0

    run.assert_called_once_with(
        [migration_server.sys.executable, "manage.py", "migrate"],
        cwd=migration_server.BASE_DIR,
        check=False,
        capture_output=True,
        text=True,
        start_new_session=True,
    )


def test_run_migrations_auto_merges_conflicts() -> None:
    """Regression: migration runner should merge graph conflicts automatically."""

    conflict = mock.Mock(
        returncode=1,
        stdout="",
        stderr=(
            "CommandError: Conflicting migrations detected; "
            "multiple leaf nodes in the migration graph"
        ),
    )
    merged = mock.Mock(returncode=0, stdout="", stderr="")
    migrated = mock.Mock(returncode=0, stdout="", stderr="")
    with (
        mock.patch.object(migration_server.sys, "platform", "linux"),
        mock.patch.object(
            migration_server.subprocess,
            "run",
            side_effect=[conflict, merged, migrated],
        ) as run,
    ):
        assert migration_server.run_migrations([]) == 0

    assert run.call_count == 3
    run.assert_any_call(
        [migration_server.sys.executable, "manage.py", "makemigrations", "--merge", "--noinput"],
        cwd=migration_server.BASE_DIR,
        check=False,
        capture_output=True,
        text=True,
        start_new_session=True,
    )


def test_parse_args_accepts_legacy_watcher_flags() -> None:
    """Legacy server flags should parse for compatibility."""

    args = migration_server.parse_args(["--interval", "2", "--debounce", "0.5", "--no-latest", "--", "--plan"])
    assert args.interval == 2
    assert args.debounce == 0.5
    assert args.latest is False
    assert args.extra_args == ["--", "--plan"]
