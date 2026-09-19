"""OCPP-only background maintenance tasks."""

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.ocpp.models import Charger, ChargerConnection


@shared_task(name="ocpp.maintenance.refresh_stale_connections")
def refresh_stale_connections() -> int:
    """Clear stale connection timestamps without interacting with host services."""
    cutoff = timezone.now() - timedelta(hours=2)
    stale_chargers = Charger.objects.filter(connected_at__lt=cutoff)
    ChargerConnection.objects.filter(charger__in=stale_chargers).delete()
    return stale_chargers.update(connected_at=None)
