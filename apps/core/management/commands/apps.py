"""Compatibility import for the app-owned ``apps`` management command."""

from apps.app.management.commands.apps import Command

__all__ = ["Command"]
