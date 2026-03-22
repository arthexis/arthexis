"""Regression tests for one-word core management command implementations."""

from __future__ import annotations

from importlib import import_module

import pytest


def test_coverage_command_module_exports_command_class() -> None:
    """The coverage command module should expose a concrete Command class."""

    module = import_module("apps.core.management.commands.coverage")

    assert hasattr(module, "Command")


def test_coverage_command_not_marked_absorbed() -> None:
    """The canonical coverage command should not be marked as an absorbed legacy shim."""

    module = import_module("apps.core.management.commands.coverage")

    assert getattr(module.Command, "arthexis_absorbed_command", False) is False
