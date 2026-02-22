"""Tests for absorbed command deprecation metadata."""

from __future__ import annotations

from django.core.management import get_commands, load_command_class


def test_absorbed_wrapper_commands_expose_replacement_metadata() -> None:
    """Legacy wrappers should publish replacement metadata via the decorator."""
    expected_replacements = {
        "watch_rfid": "rfid watch",
        "register-node": "node register",
        "check_admin": "health --target core.admin",
    }

    for command_name, replacement in expected_replacements.items():
        app_name = get_commands()[command_name]
        command = load_command_class(app_name, command_name)

        assert getattr(command.__class__, "arthexis_absorbed_command", False) is True
        assert getattr(command.__class__, "arthexis_replacement_command", "") == replacement
