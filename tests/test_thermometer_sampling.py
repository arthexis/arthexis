from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.sensors.models import Thermometer
from apps.sensors.scheduling import (
    THERMOMETER_SAMPLING_TASK_NAME,
    THERMOMETER_SAMPLING_TASK_PATH,
    ensure_thermometer_sampling_task,
)
from apps.sensors import tasks as sensor_tasks
from apps.sensors.tasks import sample_thermometers


@pytest.mark.critical
@pytest.mark.django_db
def test_sample_thermometers_records_reading(monkeypatch, tmp_path: Path):
    """Record a reading by sampling the real temperature file on disk."""
    thermometer = Thermometer.objects.create(
        name="Kitchen",
        slug="28-000000000000",
        unit="C",
        sampling_interval_seconds=60,
        is_active=True,
    )

    fixed_now = timezone.now()
    devices_path = tmp_path / "devices"
    temperature_path = devices_path / thermometer.slug / "temperature"
    temperature_path.parent.mkdir(parents=True, exist_ok=True)
    temperature_path.write_text("21500")

    monkeypatch.setattr(
        sensor_tasks.timezone,
        "localtime",
        lambda *_args, **_kwargs: fixed_now,
    )

    with override_settings(
        THERMOMETER_PATH_TEMPLATE=str(
            devices_path / "{slug}" / "temperature"
        )
    ):
        result = sample_thermometers()

    thermometer.refresh_from_db()
    assert result == {"sampled": 1, "skipped": 0, "failed": 0}
    assert thermometer.last_reading == Decimal("21.5")
    assert thermometer.last_read_at == fixed_now


@pytest.mark.django_db
@pytest.mark.integration
def test_sample_thermometers_skips_when_not_due(monkeypatch):
    fixed_now = timezone.now()
    thermometer = Thermometer.objects.create(
        name="Office",
        slug="28-000000000001",
        unit="C",
        sampling_interval_seconds=300,
        last_read_at=fixed_now,
        is_active=True,
    )

    monkeypatch.setattr(
        sensor_tasks.timezone,
        "localtime",
        lambda *_args, **_kwargs: fixed_now,
    )

    called = {"count": 0}

    def _fake_read_w1_temperature(paths):
        called["count"] += 1
        return Decimal("19.0")

    monkeypatch.setattr(sensor_tasks, "read_w1_temperature", _fake_read_w1_temperature)

    result = sample_thermometers()

    thermometer.refresh_from_db()
    assert result == {"sampled": 0, "skipped": 1, "failed": 0}
    assert thermometer.last_reading is None
    assert thermometer.last_read_at == fixed_now
    assert called["count"] == 0


@pytest.mark.django_db
@pytest.mark.integration
def test_ensure_thermometer_sampling_task_uses_seconds_interval(tmp_path: Path):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "celery.lck").touch()

    with override_settings(BASE_DIR=tmp_path):
        ensure_thermometer_sampling_task()

    task = PeriodicTask.objects.get(name=THERMOMETER_SAMPLING_TASK_NAME)
    assert task.interval is not None
    assert task.interval.every == 1
    assert task.interval.period == IntervalSchedule.SECONDS
    assert task.task == THERMOMETER_SAMPLING_TASK_PATH
