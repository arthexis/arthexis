"""Tests for the consolidated ``test`` management command."""

from __future__ import annotations

import sys

import pytest
from django.core.management import CommandError, call_command


pytestmark = pytest.mark.regression


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


