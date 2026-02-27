"""Tests for the consolidated ``migrations`` management command."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from apps.tests.management.commands.migrations import Command


pytestmark = pytest.mark.regression


def test_migrations_command_rejects_unknown_action() -> None:
    """Regression: unsupported migration actions should raise command errors."""

    command = Command()

    with pytest.raises(CommandError, match="Unsupported action"):
        command.handle(action="invalid")


def test_migration_server_subcommand_does_not_require_vscode_cli(monkeypatch) -> None:
    """Regression: ``migrations server`` should execute via Python module imports only."""

    from apps.tests.management.commands.migrations import Command

    called: dict[str, list[str]] = {}

    def fake_main(argv: list[str]) -> int:
        called["argv"] = argv
        return 0

    monkeypatch.setattr("apps.vscode.migration_server.main", fake_main)

    Command()._run_migration_server({"interval": 2.0, "latest": False})

    assert called["argv"] == ["--interval", "2.0", "--no-latest"]


