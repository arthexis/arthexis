"""Helpers for clearing cached charger status fields."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable

from django.apps import apps
from django.db import connections
from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Q
from django.utils import timezone

from . import store
from .status_display import ERROR_OK_VALUES


logger = logging.getLogger(__name__)


# Fields to reset when clearing stale status information.
STATUS_RESET_UPDATES = {
    "last_status": "",
    "last_error_code": "",
    "last_status_vendor_info": None,
    "last_status_timestamp": None,
}


def _charger_table_exists() -> bool:
    """Return ``True`` when the charger table is available on the default DB."""

    connection = connections["default"]
    try:
        with connection.cursor() as cursor:
            table_names = set(connection.introspection.table_names(cursor))
    except (OperationalError, ProgrammingError):
        return False

    charger_model = apps.get_model("ocpp", "Charger")
    return charger_model._meta.db_table in table_names


def clear_cached_statuses(charger_ids: Iterable[str] | None = None) -> int:
    """Clear cached status fields for the provided charger ids.

    When ``charger_ids`` is ``None``, all known chargers are cleared. The
    function returns the number of records updated.
    """

    charger_model = apps.get_model("ocpp", "Charger")
    connection = connections["default"]
    try:
        with connection.cursor() as cursor:
            table_names = set(connection.introspection.table_names(cursor))
    except (OperationalError, ProgrammingError):
        logger.warning("Charger table lookup failed; deferring cached status reset")
        return 0

    if charger_model._meta.db_table not in table_names:
        return 0

    try:
        queryset = charger_model.objects.all()
        if charger_ids is not None:
            queryset = queryset.filter(charger_id__in=charger_ids)
        return queryset.update(**STATUS_RESET_UPDATES)
    except (OperationalError, ProgrammingError):
        logger.warning(
            "Unable to clear cached charger statuses; schema appears to be mid-migration",
        )
        return 0


def clear_stale_cached_statuses(max_age: timedelta = timedelta(minutes=5)) -> int:
    """Clear status fields for chargers without a recent heartbeat.

    Any charger whose ``last_heartbeat`` is older than ``max_age`` (or missing)
    is treated as stale. Lock files used to flag active charging sessions are
    removed when they are older than the same threshold. The function returns
    the number of charger rows updated.
    """

    charger_model = apps.get_model("ocpp", "Charger")
    if not _charger_table_exists():
        return 0
    cutoff = timezone.now() - max_age
    try:
        stale_chargers = charger_model.objects.filter(
            Q(last_heartbeat__isnull=True) | Q(last_heartbeat__lt=cutoff)
        )

        placeholder_error_filter = Q(last_error_code__isnull=True) | Q(
            last_error_code__exact=""
        )
        for ok_value in ERROR_OK_VALUES:
            if not ok_value:
                continue
            placeholder_error_filter |= Q(last_error_code__iexact=ok_value)

        non_placeholder_fields = {
            key: value
            for key, value in STATUS_RESET_UPDATES.items()
            if key != "last_error_code"
        }
        updated = stale_chargers.exclude(placeholder_error_filter).update(
            **non_placeholder_fields
        )
        updated += stale_chargers.filter(placeholder_error_filter).update(
            **STATUS_RESET_UPDATES
        )
    except (OperationalError, ProgrammingError):
        logger.warning(
            "Unable to clear stale cached statuses; charger schema may be migrating",
        )
        return 0

    lock = store.SESSION_LOCK
    if lock.exists():
        try:
            modified = datetime.fromtimestamp(lock.stat().st_mtime, tz=timezone.utc)
        except Exception:  # pragma: no cover - defensive for invalid timestamps
            modified = None
        if modified is None or modified < cutoff:
            store.stop_session_lock()

    return updated

