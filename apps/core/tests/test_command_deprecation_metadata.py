"""Tests for absorbed command deprecation metadata."""

from __future__ import annotations

from django.core.management import get_commands, load_command_class


def test_absorbed_wrapper_commands_expose_replacement_metadata() -> None:
    """Legacy wrappers should publish replacement metadata via the decorator."""
    expected_replacements = {
        "export_rfids": "rfid export",
        "import_rfids": "rfid import",
        "rfid_doctor": "rfid doctor",
        "rfid_service": "rfid service",
        "watch_rfid": "rfid watch",
        "check_admin": "health --target core.admin",
        "check_lcd_send": "health --target core.lcd_send",
        "check_lcd_service": "health --target core.lcd_service",
        "check_next_upgrade": "health --target core.next_upgrade",
        "check_rfid": "rfid check --uid <UID>",
        "check_system_user": "health --target core.system_user",
        "check_time": "health --target core.time",
        "check_nodes": "node check",
        "lan-find-node": "node discover",
        "register-node": "node register",
        "register-node-curl": "node register_curl",
        "registration_ready": "node ready",
        "update-peer-nodes": "node peers",
    }

    registered = get_commands()
    for command_name, replacement in expected_replacements.items():
        assert command_name in registered, (
            f"Command '{command_name}' from expected_replacements is not registered; "
            "check INSTALLED_APPS and command module names."
        )
        app_name = registered[command_name]
        command = load_command_class(app_name, command_name)

        assert getattr(command.__class__, "arthexis_absorbed_command", False) is True
        assert getattr(command.__class__, "arthexis_replacement_command", "") == replacement
