"""Regression tests for one-word core management command aliases."""

from __future__ import annotations

from importlib import import_module


def test_alias_command_subclasses_legacy_command() -> None:
    """Each one-word alias should expose the corresponding legacy command implementation."""

    legacy_module = import_module("apps.core.management.commands.calculate_coverage")
    alias_module = import_module("apps.core.management.commands.coverage")

    assert issubclass(alias_module.Command, legacy_module.Command)


def test_legacy_command_is_marked_absorbed() -> None:
    """Legacy commands should be marked as absorbed into their one-word replacement names."""

    legacy_module = import_module("apps.core.management.commands.calculate_coverage")

    assert getattr(legacy_module.Command, "arthexis_absorbed_command", False) is True
    assert (
        getattr(legacy_module.Command, "arthexis_replacement_command", "") == "coverage"
    )
