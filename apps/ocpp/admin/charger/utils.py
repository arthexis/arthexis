"""Shared helper utilities for charger admin mixins."""

from ..common_imports import *


def charger_display_name(charger: Charger) -> str:
    """Return an admin-friendly display name for a charger."""
    return charger.display_name or charger.connector_id or charger.charger_id or str(charger.pk)
