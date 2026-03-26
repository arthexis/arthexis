"""Tests for desktop management command entrypoints."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def test_register_desktop_extensions_is_retired() -> None:
    """Retired command should explain the supported replacement path."""

    with pytest.raises(CommandError, match="retired"):
        call_command("register_desktop_extensions")


def test_desktop_extension_open_is_retired() -> None:
    """Legacy command should fail fast with runbook guidance."""

    with pytest.raises(CommandError, match="retired"):
        call_command("desktop_extension_open")
