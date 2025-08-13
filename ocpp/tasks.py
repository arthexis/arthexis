import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django.db.models import Q

from .models import MeterReading

logger = logging.getLogger(__name__)


@shared_task
def purge_meter_readings() -> int:
    """Delete meter readings older than 7 days.

    Readings tied to transactions without a recorded meter_stop are preserved so
    that ongoing or incomplete sessions retain their energy data.
    Returns the number of deleted readings.
    """
    cutoff = timezone.now() - timedelta(days=7)
    qs = MeterReading.objects.filter(timestamp__lt=cutoff).filter(
        Q(transaction__isnull=True) | Q(transaction__meter_stop__isnull=False)
    )
    deleted, _ = qs.delete()
    logger.info("Purged %s meter readings", deleted)
    return deleted
