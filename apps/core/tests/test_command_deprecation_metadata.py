"""Regression tests for removed absorbed management command wrappers."""

from __future__ import annotations

from pathlib import Path

from django.core.management import get_commands

from utils.command_api import CommandOptions, filtered_commands


REMOVED_WRAPPER_COMMANDS = {
    "build_pypi",
    "capture_release_state",
    "check_admin",
    "check_forwarders",
    "check_lcd_send",
    "check_lcd_service",
    "check_next_upgrade",
    "check_nodes",
    "check_pypi",
    "check_rfid",
    "check_system_user",
    "check_time",
    "clean_release_logs",
    "coverage_ocpp16",
    "coverage_ocpp201",
    "coverage_ocpp21",
    "export_rfids",
    "export_transactions",
    "import_rfids",
    "import_transactions",
    "lan-find-node",
    "lcd_animate",
    "lcd_calibrate",
    "lcd_debug",
    "lcd_plan",
    "lcd_replay",
    "lcd_write",
    "ocpp_extract",
    "ocpp_replay",
    "prepare_release",
    "register-node",
    "register-node-curl",
    "registration_ready",
    "rfid_check",
    "rfid_doctor",
    "rfid_service",
    "snapshot",
    "update-peer-nodes",
    "video_debug",
    "watch_rfid",
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
