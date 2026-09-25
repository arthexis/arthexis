"""OCPP-only background maintenance tasks."""

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.ocpp.domain.operations import (
    reconcile_configuration_operations,
    reconcile_session_operations,
)
from apps.ocpp.domain.sessions import reconcile_pending_transaction_energy
from apps.ocpp.models import Charger, ChargerConnection


@shared_task(name="ocpp.maintenance.refresh_stale_connections")
def refresh_stale_connections() -> int:
    """Clear stale connection timestamps without interacting with host services."""
    cutoff = timezone.now() - timedelta(hours=2)
    stale_chargers = Charger.objects.filter(connected_at__lt=cutoff)
    ChargerConnection.objects.filter(charger__in=stale_chargers).delete()
    return stale_chargers.update(connected_at=None)


@shared_task(name="ocpp.maintenance.reconcile_meter_energy")
def reconcile_meter_energy() -> int:
    """Repair stale transaction energy from authoritative retained meter evidence."""
    return reconcile_pending_transaction_energy()


@shared_task(name="ocpp.maintenance.reconcile_session_operations")
def reconcile_ambiguous_session_operations() -> int:
    """Resolve ambiguous remote start/stop work from retained session evidence."""
    return reconcile_session_operations()


@shared_task(name="ocpp.maintenance.reconcile_configuration_operations")
def reconcile_ambiguous_configuration_operations() -> int:
    """Resolve ambiguous configuration writes through safe observational reads."""
    return reconcile_configuration_operations()
