"""Regression tests for one-word core management command aliases."""

from __future__ import annotations

from importlib import import_module

import pytest

COMMAND_ALIASES = {
    "calculate_coverage": "coverage",
    "channel_health": "channels",
    "export_usage_analytics": "analytics",
    "offline_time": "availability",
    "report_startup": "startup",
    "send_invite": "invite",
    "set_env": "env",
    "show_changelog": "changelog",
    "show_leads": "leads",
    "update_fixtures": "fixtures",
}


@pytest.mark.parametrize("legacy_command,alias_command", COMMAND_ALIASES.items())
def test_alias_command_subclasses_legacy_command(
    legacy_command: str, alias_command: str
) -> None:
    """Each one-word alias should expose the corresponding legacy command implementation."""

    legacy_module = import_module(f"apps.core.management.commands.{legacy_command}")
    alias_module = import_module(f"apps.core.management.commands.{alias_command}")

    assert issubclass(alias_module.Command, legacy_module.Command)


@pytest.mark.parametrize("legacy_command,alias_command", COMMAND_ALIASES.items())
def test_legacy_command_is_marked_absorbed(
    legacy_command: str, alias_command: str
) -> None:
    """Legacy commands should be marked as absorbed into their one-word replacement names."""

    legacy_module = import_module(f"apps.core.management.commands.{legacy_command}")

    assert getattr(legacy_module.Command, "arthexis_absorbed_command", False) is True
    assert (
        getattr(legacy_module.Command, "arthexis_replacement_command", "")
        == alias_command
    )
