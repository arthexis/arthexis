from __future__ import annotations

import logging
from datetime import datetime

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Thermometer
from .thermometers import read_w1_temperature

logger = logging.getLogger(__name__)


def _thermometer_is_due(thermometer: Thermometer, now: datetime) -> bool:
    interval_seconds = thermometer.sampling_interval_seconds
    if interval_seconds <= 0:
        return False
    last_read_at = thermometer.last_read_at
    if last_read_at is None:
        return True
    return (now - last_read_at).total_seconds() >= interval_seconds


@shared_task(name="apps.sensors.tasks.sample_thermometers")
def sample_thermometers() -> dict[str, int]:
    now = timezone.localtime()
    sampled = 0
    skipped = 0
    failed = 0

    for thermometer in Thermometer.objects.filter(is_active=True).iterator():
        if not _thermometer_is_due(thermometer, now):
            skipped += 1
            continue

        path_template = getattr(
            settings,
            "THERMOMETER_PATH_TEMPLATE",
            "/sys/bus/w1/devices/{slug}/temperature",
        )
        device_path = path_template.format(slug=thermometer.slug)
        reading = read_w1_temperature(paths=[device_path])
        if reading is None:
            failed += 1
            logger.info(
                "Thermometer sample skipped; no reading returned for %s",
                thermometer.slug,
            )
            continue

        thermometer.record_reading(reading, read_at=timezone.localtime())
        sampled += 1

    return {"sampled": sampled, "skipped": skipped, "failed": failed}


__all__ = ["sample_thermometers"]
