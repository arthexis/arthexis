"""Tests for desktop extension services."""

from __future__ import annotations

from apps.desktop.models import RegisteredExtension
from apps.desktop.services import build_windows_registry_command


def test_build_windows_registry_command_contains_expected_parts(settings) -> None:
    """Registry command should execute manage.py desktop_extension_open."""

    settings.BASE_DIR = "/workspace/arthexis"
    extension = RegisteredExtension(pk=12, extension=".log", django_command="noop")

    command = build_windows_registry_command(extension)

    assert "desktop_extension_open" in command
    assert "--extension-id 12" in command
    assert '--filename "%1"' in command
