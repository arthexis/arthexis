"""Public OCPP charger helpers shared across non-view layers."""

from __future__ import annotations

from django.http import Http404

from apps.ocpp.models import Charger
from apps.ocpp.views.actions.common import _ensure_charger_access, _normalize_connector_slug
from apps.ocpp.views.common import _connector_set, _live_sessions


def get_charger_for_read(serial: str, connector_slug: str | None) -> tuple[Charger, str]:
    """Return an existing charger for read-only flows.

    Unlike the view helper, this function never creates missing charger rows.
    """

    try:
        normalized_serial = Charger.validate_serial(serial)
    except Exception as exc:  # pragma: no cover - defensive validation guard
        raise Http404("Charger not found") from exc

    connector_value, normalized_slug = _normalize_connector_slug(connector_slug)
    queryset = Charger.objects.filter(charger_id=normalized_serial)
    if connector_value is None:
        charger = queryset.filter(connector_id__isnull=True).order_by("pk").first()
    else:
        charger = queryset.filter(connector_id=connector_value).order_by("pk").first()
    if charger is None:
        raise Http404("Charger not found")
    return charger, normalized_slug


def connector_set(charger: Charger) -> list[Charger]:
    """Return sibling connectors ordered for presentation."""

    return _connector_set(charger)


def ensure_charger_access(user, charger: Charger) -> None:
    """Raise ``Http404`` when user access should be denied."""

    _ensure_charger_access(user, charger, request=None)


def live_sessions(charger: Charger, *, connectors: list[Charger] | None = None):
    """Return active sessions grouped by connector for ``charger``."""

    return _live_sessions(charger, connectors=connectors)

