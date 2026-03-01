"""Tests for the VS Code one-shot test runner."""

from __future__ import annotations

from unittest import mock

from apps.vscode import test_server


def test_build_pytest_command_with_extra_args() -> None:
    """Ensure pytest command includes passthrough arguments."""

    command = test_server.build_pytest_command(["-q"])
    assert command[-1] == "-q"


def test_main_strips_remainder_separator() -> None:
    """Regression: CLI should ignore argparse's ``--`` remainder separator."""

    with mock.patch.object(test_server, "run_tests", return_value=0) as runner:
        test_server.main(["--", "-q"])

    runner.assert_called_once_with(["-q"])


def test_run_tests_returns_subprocess_exit_code() -> None:
    """Run result should mirror the subprocess return code."""

    completed = mock.Mock(returncode=2)
    with mock.patch.object(test_server.subprocess, "run", return_value=completed) as run:
        assert test_server.run_tests([]) == 2

    run.assert_called_once_with(
        [test_server.sys.executable, "-m", "pytest"],
        cwd=test_server.BASE_DIR,
        check=False,
    )


def test_parse_args_accepts_legacy_watcher_flags() -> None:
    """Legacy server flags should parse for compatibility."""

    args = test_server.parse_args(["--interval", "2", "--debounce", "0.5", "--no-latest", "--", "-q"])
    assert args.interval == 2
    assert args.debounce == 0.5
    assert args.latest is False
    assert args.extra_args == ["--", "-q"]
