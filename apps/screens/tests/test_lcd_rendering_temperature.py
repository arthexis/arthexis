from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.screens.lcd_screen import rendering
from apps.sensors.models import Thermometer

pytestmark = pytest.mark.django_db


def test_lcd_temperature_label_from_sensors_uses_latest_active_reading() -> None:
    first = Thermometer.objects.create(
        name="First",
        slug="first",
        unit="C",
        is_active=True,
    )
    latest = Thermometer.objects.create(
        name="Second",
        slug="second",
        unit="C",
        is_active=True,
    )
    first.record_reading(
        Decimal("20.1"),
        read_at=timezone.now() - timedelta(minutes=5),
    )
    latest.record_reading(Decimal("21.6"), read_at=timezone.now())

    label = rendering._lcd_temperature_label_from_sensors()

    assert label == latest.format_lcd_reading()


def test_lcd_temperature_label_from_sensors_ignores_inactive_and_empty() -> None:
    Thermometer.objects.create(
        name="Inactive",
        slug="inactive",
        unit="C",
        last_reading=Decimal("19.5"),
        is_active=False,
    )
    Thermometer.objects.create(
        name="Empty",
        slug="empty",
        unit="C",
        last_reading=None,
        is_active=True,
    )

    assert rendering._lcd_temperature_label_from_sensors() is None
