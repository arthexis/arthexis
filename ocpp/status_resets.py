"""Helpers for clearing cached charger status fields."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from django.apps import apps
from django.db.models import Q
from django.utils import timezone

from . import store


# Fields to reset when clearing stale status information.
STATUS_RESET_UPDATES = {
    "last_status": "",
    "last_error_code": "",
    "last_status_vendor_info": None,
    "last_status_timestamp": None,
}


def clear_cached_statuses(charger_ids: Iterable[str] | None = None) -> int:
    """Clear cached status fields for the provided charger ids.

    When ``charger_ids`` is ``None``, all known chargers are cleared. The
    function returns the number of records updated.
    """

    charger_model = apps.get_model("ocpp", "Charger")
    queryset = charger_model.objects.all()
    if charger_ids is not None:
        queryset = queryset.filter(charger_id__in=charger_ids)
    return queryset.update(**STATUS_RESET_UPDATES)


def clear_stale_cached_statuses(max_age: timedelta = timedelta(minutes=5)) -> int:
    """Clear status fields for chargers without a recent heartbeat.

    Any charger whose ``last_heartbeat`` is older than ``max_age`` (or missing)
    is treated as stale. Lock files used to flag active charging sessions are
    removed when they are older than the same threshold. The function returns
    the number of charger rows updated.
    """

    charger_model = apps.get_model("ocpp", "Charger")
    cutoff = timezone.now() - max_age
    stale_chargers = charger_model.objects.filter(
        Q(last_heartbeat__isnull=True) | Q(last_heartbeat__lt=cutoff)
    )
    updated = stale_chargers.update(**STATUS_RESET_UPDATES)

    lock = store.SESSION_LOCK
    if lock.exists():
        try:
            modified = datetime.fromtimestamp(lock.stat().st_mtime, tz=timezone.utc)
        except Exception:  # pragma: no cover - defensive for invalid timestamps
            modified = None
        if modified is None or modified < cutoff:
            store.stop_session_lock()

    return updated

