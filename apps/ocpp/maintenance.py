"""Operational maintenance helpers for OCPP."""

from __future__ import annotations

import logging

from django.apps import apps
from django.db import connections
from django.db.utils import OperationalError, ProgrammingError

from .status_resets import clear_cached_statuses

logger = logging.getLogger(__name__)


def reset_cached_statuses(*, connection_alias: str = "default") -> int:
    """Clear persisted charger status fields when the OCPP schema is available."""

    connection = connections[connection_alias]
    charger_model = apps.get_model("ocpp", "Charger")

    try:
        with connection.cursor() as cursor:
            table_names = set(connection.introspection.table_names(cursor))
    except (OperationalError, ProgrammingError):
        logger.debug("Skipping cached status reset; database unavailable", exc_info=True)
        return 0

    if charger_model._meta.db_table not in table_names:
        return 0

    try:
        cleared = clear_cached_statuses()
    except (OperationalError, ProgrammingError):
        logger.debug("Skipping cached status reset; schema unavailable", exc_info=True)
        return 0

    if cleared:
        logger.info("Cleared cached charger statuses for %s charge points", cleared)
    return cleared
