"""Tests for the consolidated ``migrations`` management command."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from apps.tests.management.commands.migrations import Command


pytestmark = pytest.mark.regression


def test_migrations_run_delegates_to_migrate(monkeypatch) -> None:
    """Regression: ``migrations run`` should invoke ``migrate`` with target."""

    captured: dict[str, object] = {}

    def fake_call_command(name, *args, **kwargs):
        captured["name"] = name
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr("apps.tests.management.commands.migrations.call_command", fake_call_command)

    call_command("migrations", "run", "users", "0001_initial", "--database", "default")

    assert captured["name"] == "migrate"
    assert captured["args"] == ("users", "0001_initial")
    assert captured["kwargs"]["database"] == "default"


def test_migrations_check_delegates_to_makemigrations(monkeypatch) -> None:
    """Regression: ``migrations check`` should enforce dry-run checks."""

    captured: dict[str, object] = {}

    def fake_call_command(name, *args, **kwargs):
        captured["name"] = name
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr("apps.tests.management.commands.migrations.call_command", fake_call_command)

    call_command("migrations", "check")

    assert captured["name"] == "makemigrations"
    assert captured["kwargs"]["check"] is True
    assert captured["kwargs"]["dry_run"] is True


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


def test_migrations_make_delegates_to_makemigrations(monkeypatch) -> None:
    """Regression: ``migrations make`` should delegate labels and flags."""

    captured: dict[str, object] = {}

    def fake_call_command(name, *args, **kwargs):
        captured["name"] = name
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr("apps.tests.management.commands.migrations.call_command", fake_call_command)

    call_command("migrations", "make", "app1", "app2", "--check", "--dry-run")

    assert captured["name"] == "makemigrations"
    assert captured["args"] == ("app1", "app2")
    assert captured["kwargs"] == {"check": True, "dry_run": True}
