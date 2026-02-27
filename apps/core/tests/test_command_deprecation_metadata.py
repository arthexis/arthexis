"""Regression tests for removed absorbed management command wrappers."""

from __future__ import annotations

from pathlib import Path

from django.core.management import get_commands

from utils.command_api import CommandOptions, filtered_commands


REMOVED_WRAPPER_COMMANDS = {
    "export_rfids",
    "import_rfids",
    "rfid_doctor",
    "rfid_service",
    "watch_rfid",
    "check_admin",
    "check_lcd_send",
    "check_lcd_service",
    "check_next_upgrade",
    "check_rfid",
    "check_system_user",
    "check_time",
    "check_nodes",
    "lan-find-node",
    "register-node",
    "register-node-curl",
    "registration_ready",
    "update-peer-nodes",
    "lcd_animate",
    "lcd_calibrate",
    "lcd_debug",
    "lcd_plan",
    "lcd_replay",
    "lcd_write",
    "snapshot",
    "video_debug",
    "build_pypi",
    "check_pypi",
    "clean_release_logs",
    "prepare_release",
    "capture_release_state",
    "coverage_ocpp16",
    "coverage_ocpp201",
    "coverage_ocpp21",
    "import_transactions",
    "export_transactions",
    "ocpp_extract",
    "ocpp_replay",
    "check_forwarders",
    "rfid_check",
}


def test_removed_wrappers_are_not_registered_commands() -> None:
    """Regression: removed wrapper entrypoints should not register with Django."""

    registered = set(get_commands())
    assert REMOVED_WRAPPER_COMMANDS.isdisjoint(registered)


def test_filtered_commands_excludes_removed_wrappers_even_with_deprecated() -> None:
    """Removed wrappers should not reappear when ``--deprecated`` filtering is enabled."""

    default_commands = set(filtered_commands(Path.cwd(), CommandOptions(deprecated=False)))
    deprecated_commands = set(filtered_commands(Path.cwd(), CommandOptions(deprecated=True)))

    assert REMOVED_WRAPPER_COMMANDS.isdisjoint(default_commands)
    assert REMOVED_WRAPPER_COMMANDS.isdisjoint(deprecated_commands)
