"""Maintenance tasks for OCPP data retention."""

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.ocpp.models import MeterValue

logger = logging.getLogger(__name__)


@shared_task(name="apps.ocpp.tasks.purge_meter_values")
def purge_meter_values() -> int:
    """Delete meter values older than 7 days.

    Values tied to transactions without a recorded meter stop are preserved so
    ongoing or incomplete sessions retain their energy data.
    """

    cutoff = timezone.now() - timedelta(days=7)
    queryset = MeterValue.objects.filter(timestamp__lt=cutoff).filter(
        Q(transaction__isnull=True) | Q(transaction__meter_stop__isnull=False)
    )
    deleted, _ = queryset.delete()
    logger.info("Purged %s meter values", deleted)
    return deleted


purge_meter_readings = purge_meter_values
