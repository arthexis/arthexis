"""Shared helper utilities for charger admin mixins."""

from ..common_imports import *


def charger_display_name(charger: Charger) -> str:
    """Return an admin-friendly display name for a charger."""
    if charger.display_name:
        return charger.display_name
    if charger.location:
        return charger.location.name
    if charger.connector_id is not None:
        return str(charger.connector_id)
    return charger.charger_id or str(charger.pk)
