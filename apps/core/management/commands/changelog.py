"""Compatibility import for the release-owned changelog command."""

from apps.release.management.commands.changelog import Command

__all__ = ["Command"]
