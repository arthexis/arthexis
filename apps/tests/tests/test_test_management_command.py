"""Tests for the consolidated ``test`` management command."""

from __future__ import annotations

import sys

import pytest
from django.core.management import CommandError, call_command


pytestmark = pytest.mark.regression


def test_test_run_delegates_to_pytest(monkeypatch) -> None:
    """Regression: ``test run`` should forward arguments to pytest execution."""

    captured: dict[str, list[str]] = {}

    def fake_run_pytest(_self, pytest_args):
        captured["args"] = list(pytest_args)

    monkeypatch.setattr("apps.tests.management.commands.test.Command._run_pytest", fake_run_pytest)

    call_command("test", "run", "--", "-k", "smoke")

    assert captured["args"] == ["--", "-k", "smoke"]


def test_test_server_invokes_vscode_test_server(monkeypatch) -> None:
    """Regression: ``test server`` should call the test server helper."""

    called: dict[str, object] = {}

    def fake_server(_self, *, interval: float, debounce: float, latest: bool) -> None:
        called.update(interval=interval, debounce=debounce, latest=latest)

    monkeypatch.setattr(
        "apps.tests.management.commands.test.Command._run_test_server",
        fake_server,
    )

    call_command("test", "server", "--interval", "2", "--debounce", "3", "--no-latest")

    assert called == {"interval": 2.0, "debounce": 3.0, "latest": False}


def test_test_command_rejects_unknown_action() -> None:
    """Regression: unsupported actions should raise a command error."""

    from apps.tests.management.commands.test import Command

    with pytest.raises(CommandError, match="Unsupported action"):
        Command().handle(action="invalid", pytest_args=[])


def test_test_server_subcommand_does_not_require_vscode_cli(monkeypatch) -> None:
    """Regression: ``test server`` should execute via Python module imports only."""

    from apps.tests.management.commands.test import Command

    called: dict[str, list[str]] = {}

    def fake_main(argv: list[str]) -> int:
        called["argv"] = argv
        return 0

    monkeypatch.setattr("apps.vscode.test_server.main", fake_main)

    Command()._run_test_server(interval=1.5, debounce=0.5, latest=True)

    assert called["argv"] == ["--interval", "1.5", "--debounce", "0.5", "--latest"]


def test_run_pytest_strips_double_dash_separator(monkeypatch) -> None:
    """Regression: ``_run_pytest`` should drop a leading ``--`` separator."""

    from apps.tests.management.commands.test import Command

    captured: dict[str, object] = {}

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr("apps.tests.management.commands.test.subprocess.run", fake_run)

    Command()._run_pytest(["--", "-k", "smoke"])

    assert captured["cmd"][0:3] == [sys.executable, "-m", "pytest"]
    assert captured["cmd"][-2:] == ["-k", "smoke"]
