"""Helpers for clearing cached charger status fields."""

from __future__ import annotations

from typing import Iterable

from django.apps import apps


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

