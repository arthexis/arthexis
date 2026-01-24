from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .models import Thermometer
from .thermometers import read_w1_temperature

logger = logging.getLogger(__name__)


def _thermometer_is_due(thermometer: Thermometer, now) -> bool:
    interval_seconds = thermometer.sampling_interval_seconds
    if interval_seconds <= 0:
        return False
    last_read_at = thermometer.last_read_at
    if last_read_at is None:
        return True
    return (now - last_read_at).total_seconds() >= interval_seconds


@shared_task(name="apps.sensors.tasks.sample_thermometers")
def sample_thermometers() -> dict[str, int]:
    now = timezone.now()
    sampled = 0
    skipped = 0
    failed = 0

    for thermometer in Thermometer.objects.filter(is_active=True):
        if not _thermometer_is_due(thermometer, now):
            skipped += 1
            continue

        device_path = f"/sys/bus/w1/devices/{thermometer.slug}/temperature"
        reading = read_w1_temperature(paths=[device_path])
        if reading is None:
            failed += 1
            logger.info(
                "Thermometer sample skipped; no reading returned for %s",
                thermometer.slug,
            )
            continue

        thermometer.record_reading(reading, read_at=now)
        sampled += 1

    return {"sampled": sampled, "skipped": skipped, "failed": failed}


__all__ = ["sample_thermometers"]
