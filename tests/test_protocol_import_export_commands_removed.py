"""Regression checks for removed protocol import/export management commands."""

from __future__ import annotations

from django.core.management import get_commands


def test_protocol_import_export_commands_are_not_registered_regression() -> None:
    """Protocol import/export commands should no longer be discoverable."""

    commands = get_commands()

    assert "import_protocol" not in commands
    assert "export_protocol" not in commands
