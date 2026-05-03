from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

import apps.screens.lcd_screen as lcd_package
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


def test_lcd_temperature_label_from_sysfs_uses_configured_i2c_path(
    monkeypatch, settings
) -> None:
    settings.THERMOMETER_SOURCE = "i2c"
    settings.THERMOMETER_I2C_PATH_TEMPLATE = "/sys/bus/i2c/devices/1-0068/temp1_input"
    captured = {}

    def fake_format_temperature(*, source, w1_paths, i2c_paths):
        captured["source"] = source
        captured["w1_paths"] = w1_paths
        captured["i2c_paths"] = i2c_paths
        return "31.3C"

    monkeypatch.setattr(
        "apps.sensors.thermometers.format_temperature",
        fake_format_temperature,
    )

    assert rendering._lcd_temperature_label_from_sysfs() == "31.3C"
    assert captured == {
        "source": "i2c",
        "w1_paths": None,
        "i2c_paths": ["/sys/bus/i2c/devices/1-0068/temp1_input"],
    }


def test_select_low_payload_includes_temperature_on_main_screen(
    monkeypatch, tmp_path
) -> None:
    now = timezone.now()
    monkeypatch.setattr(
        lcd_package,
        "_uptime_seconds",
        lambda base_dir, now: 3661,
    )
    monkeypatch.setattr(
        lcd_package,
        "_on_seconds",
        lambda base_dir, now: 61,
    )
    monkeypatch.setattr(lcd_package, "_ap_mode_enabled", lambda: False)
    monkeypatch.setattr(lcd_package, "_internet_interface_label", lambda: "NET")
    monkeypatch.setattr(lcd_package, "_lcd_temperature_label", lambda: "88.2F")

    payload = rendering._select_low_payload(
        rendering.locks.LockPayload("", "", rendering.locks.DEFAULT_SCROLL_MS),
        base_dir=tmp_path,
        now=now,
    )

    assert payload.line1 == "UP 0d1h1m"
    assert payload.line2 == "ON 1m1s 88.2F NET"
