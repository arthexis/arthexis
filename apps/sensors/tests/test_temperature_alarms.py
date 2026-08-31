from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.notifications import LcdChannel
from apps.sensors.models import Thermometer, ThermometerAlarmEvent
from apps.sensors.tasks import sample_thermometers
from apps.sensors.temperature_alarms import evaluate_temperature_alarm

pytestmark = pytest.mark.django_db


class _FakeNotificationManager:
    calls: list[dict[str, object]] = []

    def send(self, subject, body="", **kwargs):
        self.calls.append({"subject": subject, "body": body, **kwargs})
        return True


@pytest.fixture(autouse=True)
def alarm_delivery_mocks(monkeypatch):
    lcd_calls: list[dict[str, object]] = []
    net_messages: list[dict[str, object]] = []

    class FakeNotificationManager(_FakeNotificationManager):
        calls = lcd_calls

    def fake_broadcast(subject, body, **kwargs):
        net_messages.append({"subject": subject, "body": body, **kwargs})
        return None

    monkeypatch.setattr(
        "apps.sensors.temperature_alarms.notification_manager",
        FakeNotificationManager(),
    )
    monkeypatch.setattr(
        "apps.sensors.temperature_alarms.NetMessage.broadcast",
        fake_broadcast,
    )

    return {
        "lcd": lcd_calls,
        "net_messages": net_messages,
    }


def _thermometer(**kwargs) -> Thermometer:
    defaults = {
        "name": "SoC Temperature",
        "slug": "soc-temperature",
        "kind": Thermometer.Kind.SOC,
        "unit": "C",
        "alarm_enabled": True,
        "alarm_warning_threshold_c": Decimal("70.00"),
        "alarm_critical_threshold_c": Decimal("80.00"),
        "alarm_repeat_seconds": 900,
    }
    defaults.update(kwargs)
    return Thermometer.objects.create(**defaults)


def test_temperature_alarm_emits_warning_and_critical_events(alarm_delivery_mocks):
    thermometer = _thermometer()
    now = timezone.now()

    warning = evaluate_temperature_alarm(
        thermometer,
        Decimal("72.25"),
        read_at=now,
    )
    thermometer.refresh_from_db()
    critical = evaluate_temperature_alarm(
        thermometer,
        Decimal("82.50"),
        read_at=now + timedelta(seconds=30),
    )

    events = list(ThermometerAlarmEvent.objects.order_by("created"))
    assert warning.status == "emitted"
    assert critical.status == "emitted"
    assert [event.level for event in events] == [
        ThermometerAlarmEvent.Level.WARNING,
        ThermometerAlarmEvent.Level.CRITICAL,
    ]
    assert events[0].threshold == Decimal("70.00")
    assert events[1].threshold == Decimal("80.00")
    assert alarm_delivery_mocks["lcd"] == []
    assert alarm_delivery_mocks["net_messages"][0]["subject"] == "TEMP WARNING"
    assert alarm_delivery_mocks["net_messages"][1]["subject"] == "TEMP CRITICAL"
    assert alarm_delivery_mocks["net_messages"][1]["lcd_channel_type"] == (
        LcdChannel.HIGH.value
    )


def test_temperature_alarm_rate_limits_repeated_same_level():
    thermometer = _thermometer(alarm_net_message_enabled=False)
    now = timezone.now()

    first = evaluate_temperature_alarm(thermometer, Decimal("72.0"), read_at=now)
    thermometer.refresh_from_db()
    second = evaluate_temperature_alarm(
        thermometer,
        Decimal("73.0"),
        read_at=now + timedelta(seconds=60),
    )
    thermometer.refresh_from_db()
    third = evaluate_temperature_alarm(
        thermometer,
        Decimal("74.0"),
        read_at=now + timedelta(seconds=901),
    )

    assert first.status == "emitted"
    assert second.status == "rate-limited"
    assert third.status == "emitted"
    assert ThermometerAlarmEvent.objects.count() == 2


def test_temperature_alarm_sends_direct_lcd_when_net_message_is_disabled(
    alarm_delivery_mocks,
):
    thermometer = _thermometer(alarm_net_message_enabled=False)

    evaluate_temperature_alarm(thermometer, Decimal("72.0"))
    event = ThermometerAlarmEvent.objects.get()

    assert event.lcd_notified is True
    assert alarm_delivery_mocks["lcd"][0]["subject"] == "TEMP WARNING"
    assert alarm_delivery_mocks["net_messages"] == []


def test_temperature_alarm_does_not_send_lcd_metadata_when_lcd_disabled(
    alarm_delivery_mocks,
):
    thermometer = _thermometer(alarm_lcd_enabled=False)

    evaluate_temperature_alarm(thermometer, Decimal("72.0"))
    event = ThermometerAlarmEvent.objects.get()

    assert event.lcd_notified is False
    assert alarm_delivery_mocks["lcd"] == []
    assert alarm_delivery_mocks["net_messages"][0]["lcd_channel_type"] == "none"


def test_temperature_alarm_records_recovery_after_high_temperature():
    thermometer = _thermometer()
    now = timezone.now()

    evaluate_temperature_alarm(thermometer, Decimal("82.0"), read_at=now)
    thermometer.refresh_from_db()
    result = evaluate_temperature_alarm(
        thermometer,
        Decimal("58.0"),
        read_at=now + timedelta(minutes=5),
    )
    thermometer.refresh_from_db()
    event = ThermometerAlarmEvent.objects.latest("created")

    assert result.status == "recovered"
    assert result.level == Thermometer.AlarmLevel.NORMAL
    assert thermometer.last_alarm_level == Thermometer.AlarmLevel.NORMAL
    assert event.level == ThermometerAlarmEvent.Level.RECOVERY
    assert event.threshold is None
    assert "recovered" in event.message


def test_temperature_alarm_skips_disabled_and_missing_readings():
    disabled = _thermometer(alarm_enabled=False)
    missing = _thermometer(slug="missing-soc-temperature")

    disabled_result = evaluate_temperature_alarm(disabled, Decimal("99.0"))
    missing_result = evaluate_temperature_alarm(missing, None)

    assert disabled_result.status == "disabled"
    assert missing_result.status == "missing-reading"
    assert ThermometerAlarmEvent.objects.count() == 0


def test_temperature_alarm_requires_threshold_when_enabled():
    thermometer = Thermometer(
        name="Invalid Alarm",
        slug="invalid-alarm",
        kind=Thermometer.Kind.SOC,
        unit="C",
        alarm_enabled=True,
    )

    with pytest.raises(ValidationError) as exc_info:
        thermometer.full_clean()

    assert "alarm_enabled" in exc_info.value.message_dict


def test_temperature_alarm_db_rejects_invalid_threshold_order():
    with pytest.raises(IntegrityError), transaction.atomic():
        _thermometer(
            slug="invalid-threshold-order",
            alarm_warning_threshold_c=Decimal("80.00"),
            alarm_critical_threshold_c=Decimal("70.00"),
        )


def test_temperature_alarm_db_rejects_enabled_alarm_without_thresholds():
    with pytest.raises(IntegrityError), transaction.atomic():
        _thermometer(
            slug="enabled-without-thresholds",
            alarm_warning_threshold_c=None,
            alarm_critical_threshold_c=None,
        )


def test_temperature_alarm_db_rejects_invalid_threshold_order_update():
    thermometer = _thermometer(slug="invalid-threshold-order-update")

    with pytest.raises(IntegrityError), transaction.atomic():
        Thermometer.objects.filter(pk=thermometer.pk).update(
            alarm_warning_threshold_c=Decimal("80.00"),
            alarm_critical_threshold_c=Decimal("70.00"),
        )


def test_temperature_alarm_refetches_state_for_rate_limit():
    thermometer = _thermometer(alarm_net_message_enabled=False)
    stale = Thermometer.objects.get(pk=thermometer.pk)
    now = timezone.now()

    evaluate_temperature_alarm(thermometer, Decimal("72.0"), read_at=now)
    stale_result = evaluate_temperature_alarm(
        stale,
        Decimal("73.0"),
        read_at=now + timedelta(seconds=60),
    )

    assert stale_result.status == "rate-limited"
    assert ThermometerAlarmEvent.objects.count() == 1


def test_sample_thermometers_records_alarm_event_from_due_sample(
    monkeypatch,
    alarm_delivery_mocks,
) -> None:
    thermometer = _thermometer()

    monkeypatch.setattr(
        "apps.sensors.tasks.read_soc_temperature",
        lambda: Decimal("82.062"),
    )

    result = sample_thermometers()
    thermometer.refresh_from_db()
    event = ThermometerAlarmEvent.objects.get()

    assert result == {"sampled": 1, "skipped": 0, "failed": 0}
    assert thermometer.last_reading == Decimal("82.06")
    assert thermometer.last_alarm_level == Thermometer.AlarmLevel.CRITICAL
    assert event.level == ThermometerAlarmEvent.Level.CRITICAL
    assert event.lcd_notified is True
    assert alarm_delivery_mocks["net_messages"]
