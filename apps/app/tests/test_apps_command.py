"""Tests for the application-registry management command."""

from __future__ import annotations

import pytest
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError


def test_apps_lists_known_label(capsys):
    call_command("apps")
    output = capsys.readouterr().out

    assert "- core (apps.core)" in output
    assert "- app (apps.app)" in output


def test_apps_show_flags_for_single_app(capsys):
    call_command("apps", "--app", "app", "--show-flags")
    output = capsys.readouterr().out

    assert "flags:" in output
    assert "--reload-migrations" in output


def test_apps_reload_migrations_requires_confirmation():
    with pytest.raises(CommandError, match="requires --yes"):
        call_command("apps", "--app", "app", "--reload-migrations")


def test_apps_reload_migrations_invokes_migrate(monkeypatch):
    calls: list[tuple[str, tuple[object, ...]]] = []

    def _fake_call_command(name, *args, **kwargs):
        calls.append((name, args))

    monkeypatch.setattr(
        "apps.app.management.commands.apps.call_command", _fake_call_command
    )

    call_command("apps", "--app", "app", "--reload-migrations", "--yes")

    assert calls == [("migrate", ("app", "zero")), ("migrate", ())]


def test_apps_show_commands_reports_app_owned_commands(capsys):
    call_command("apps", "--app", "app", "--show-commands")
    output = capsys.readouterr().out

    assert "commands:" in output
    assert "apps" in output
    assert "enabled_apps_lock" in output


def test_registry_commands_are_owned_by_apps_app():
    commands = get_commands()

    assert commands["apps"] == "apps.app"
    assert commands["enabled_apps_lock"] == "apps.app"
