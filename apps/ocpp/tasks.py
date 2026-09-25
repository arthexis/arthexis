"""OCPP-only background maintenance tasks."""

from datetime import timedelta

from asgiref.sync import async_to_sync
from celery import shared_task
from django.utils import timezone

from apps.ocpp.domain.operations import (
    pending_configuration_observation_ids,
    reconcile_availability_operations,
    reconcile_configuration_operations,
    reconcile_reservation_operations,
    reconcile_session_operations,
)
from apps.ocpp.domain.sessions import reconcile_pending_transaction_energy
from apps.ocpp.models import Charger, ChargerConnection, ProtocolOperation
from apps.ocpp.transport.operations import enqueue_existing_operation


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
    """Resolve ambiguous configuration writes and dispatch their observations."""
    resolved = reconcile_configuration_operations()
    for operation_id in pending_configuration_observation_ids():
        operation = ProtocolOperation.objects.select_related("charger").get(pk=operation_id)
        async_to_sync(enqueue_existing_operation)(operation)
    return resolved


@shared_task(name="ocpp.maintenance.reconcile_availability_operations")
def reconcile_ambiguous_availability_operations() -> int:
    """Resolve ambiguous connector-scoped availability writes from fresh status evidence."""
    return reconcile_availability_operations()


@shared_task(name="ocpp.maintenance.reconcile_reservation_operations")
def reconcile_ambiguous_reservation_operations() -> int:
    """Resolve ambiguous reservation mutations from fresh retained domain state."""
    return reconcile_reservation_operations()
