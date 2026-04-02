"""Operational maintenance helpers for the sites app."""

from __future__ import annotations

import logging

from django.db import DatabaseError

from .models import ViewHistory

logger = logging.getLogger(__name__)


def coerce_retention_days(days: int) -> int:
    """Return a safe minimum retention window for view-history purges."""

    return max(1, days)


def purge_view_history(*, days: int = 15) -> int:
    """Remove stale :class:`apps.sites.models.ViewHistory` entries."""

    days = coerce_retention_days(days)
    try:
        deleted = ViewHistory.purge_older_than(days=days)
    except DatabaseError:
        logger.debug("Skipping view history purge; database unavailable", exc_info=True)
        return 0

    if deleted:
        logger.info("Purged %s view history entries older than %s days", deleted, days)
    return deleted
