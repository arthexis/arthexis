"""Suite feature helpers for OCPP forwarding controls."""

from __future__ import annotations

from django.core.exceptions import AppRegistryNotReady
from django.db.utils import OperationalError, ProgrammingError


OCPP_FORWARDER_FEATURE_SLUG = "ocpp-forwarder"


def ocpp_forwarder_enabled(*, default: bool = True) -> bool:
    """Return whether OCPP forwarding operations are globally enabled."""

    try:
        from apps.features.models import Feature

        is_enabled = (
            Feature.objects.filter(slug=OCPP_FORWARDER_FEATURE_SLUG)
            .values_list("is_enabled", flat=True)
            .first()
        )
    except (
        AppRegistryNotReady,
        ImportError,
        OperationalError,
        ProgrammingError,
        RuntimeError,
    ):
        return default

    if is_enabled is None:
        return default
    return bool(is_enabled)


__all__ = ["OCPP_FORWARDER_FEATURE_SLUG", "ocpp_forwarder_enabled"]
