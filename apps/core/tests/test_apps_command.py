"""Tests for the apps special management command."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def test_apps_lists_known_label(capsys):
    """apps command should include the core app in default listing."""

    call_command("apps")
    output = capsys.readouterr().out

    assert "- core (apps.core)" in output


def test_apps_show_flags_for_single_app(capsys):
    """apps --show-flags should print the app command flag guide."""

    call_command("apps", "--app", "core", "--show-flags")
    output = capsys.readouterr().out

    assert "flags:" in output
    assert "--reload-migrations" in output


def test_apps_reload_migrations_requires_confirmation():
    """apps --reload-migrations should require --yes for safety."""

    with pytest.raises(CommandError, match="requires --yes"):
        call_command("apps", "--app", "core", "--reload-migrations")


def test_apps_reload_migrations_invokes_migrate(monkeypatch):
    """apps --reload-migrations should call migrate zero then restore full graph."""

    calls: list[tuple[str, tuple[object, ...]]] = []

    def _fake_call_command(name, *args, **kwargs):
        calls.append((name, args))

    monkeypatch.setattr("apps.core.management.commands.apps.call_command", _fake_call_command)

    call_command("apps", "--app", "core", "--reload-migrations", "--yes")

    assert calls == [("migrate", ("core", "zero")), ("migrate", ())]


def test_apps_show_commands(capsys):
    """apps --show-commands should list management commands for the app."""

    call_command("apps", "--app", "core", "--show-commands")
    output = capsys.readouterr().out

    assert "commands:" in output
    assert "apps" in output
