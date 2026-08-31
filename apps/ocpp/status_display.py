"""Shared status display constants for charger views and admin."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _


# Map of normalized OCPP status values to human readable labels and colors.
STATUS_BADGE_MAP: dict[str, tuple[str, str]] = {
    "available": (_("Available"), "#0d6efd"),
    "preparing": (_("Preparing"), "#0d6efd"),
    "charging": (_("Charging"), "#198754"),
    "suspendedevse": (_("Suspended (EVSE)"), "#fd7e14"),
    "suspendedev": (_("Suspended (EV)"), "#fd7e14"),
    "finishing": (_("Finishing"), "#20c997"),
    "faulted": (_("Faulted"), "#dc3545"),
    "unavailable": (_("Unavailable"), "#6c757d"),
    "reserved": (_("Reserved"), "#6f42c1"),
    "occupied": (_("Occupied"), "#0dcaf0"),
    "outofservice": (_("Out of Service"), "#6c757d"),
}


# Error codes that indicate "no error" according to the OCPP specification.
ERROR_OK_VALUES = {"", "noerror", "no_error"}

