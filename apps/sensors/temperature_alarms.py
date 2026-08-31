from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.notifications import LcdChannel
from apps.core.notifications import manager as notification_manager
from apps.nodes.models import NetMessage

from .models import Thermometer, ThermometerAlarmEvent

logger = logging.getLogger(__name__)

ALARM_LEVELS = {
    Thermometer.AlarmLevel.NORMAL: 0,
    Thermometer.AlarmLevel.WARNING: 1,
    Thermometer.AlarmLevel.CRITICAL: 2,
}


@dataclass(frozen=True)
class TemperatureAlarmResult:
    status: str
    level: str
    event: ThermometerAlarmEvent | None = None

    @property
    def emitted(self) -> bool:
        return self.event is not None


def _alarm_level_for_reading(thermometer: Thermometer, reading: Decimal) -> str:
    critical = thermometer.alarm_critical_threshold_c
    if critical is not None and reading >= critical:
        return Thermometer.AlarmLevel.CRITICAL

    warning = thermometer.alarm_warning_threshold_c
    if warning is not None and reading >= warning:
        return Thermometer.AlarmLevel.WARNING

    return Thermometer.AlarmLevel.NORMAL


def _threshold_for_level(thermometer: Thermometer, level: str) -> Decimal | None:
    if level == Thermometer.AlarmLevel.CRITICAL:
        return thermometer.alarm_critical_threshold_c
    if level == Thermometer.AlarmLevel.WARNING:
        return thermometer.alarm_warning_threshold_c
    return None


def _alarm_event_level(level: str) -> str:
    if level == Thermometer.AlarmLevel.CRITICAL:
        return ThermometerAlarmEvent.Level.CRITICAL
    if level == Thermometer.AlarmLevel.WARNING:
        return ThermometerAlarmEvent.Level.WARNING
    return ThermometerAlarmEvent.Level.RECOVERY


def _format_reading(reading: Decimal) -> str:
    return f"{reading:.1f}C"


def _format_threshold(threshold: Decimal | None) -> str:
    if threshold is None:
        return ""
    return f"{threshold:.1f}C"


def _build_alarm_message(
    *,
    thermometer: Thermometer,
    level: str,
    reading: Decimal,
    threshold: Decimal | None,
) -> tuple[str, str]:
    label = thermometer.name or thermometer.slug
    reading_label = _format_reading(reading)
    if level == Thermometer.AlarmLevel.NORMAL:
        return "TEMP OK", f"{label} recovered to {reading_label}"
    level_label = "CRITICAL" if level == Thermometer.AlarmLevel.CRITICAL else "WARNING"
    threshold_label = _format_threshold(threshold)
    if threshold_label:
        return f"TEMP {level_label}", f"{label} {reading_label} >= {threshold_label}"
    return f"TEMP {level_label}", f"{label} {reading_label}"


def _rate_limited(thermometer: Thermometer, *, level: str, now: datetime) -> bool:
    if (thermometer.last_alarm_level or Thermometer.AlarmLevel.NORMAL) != level:
        return False
    if thermometer.last_alarm_at is None:
        return False
    repeat_seconds = max(int(thermometer.alarm_repeat_seconds or 1), 1)
    return (now - thermometer.last_alarm_at).total_seconds() < repeat_seconds


def _deliver_alarm(
    *,
    thermometer: Thermometer,
    event: ThermometerAlarmEvent,
    subject: str,
    body: str,
) -> None:
    update_fields: list[str] = []
    lcd_via_net_message = (
        thermometer.alarm_lcd_enabled and thermometer.alarm_net_message_enabled
    )

    if thermometer.alarm_lcd_enabled and not lcd_via_net_message:
        try:
            event.lcd_notified = notification_manager.send(
                subject,
                body,
                sticky=event.level != ThermometerAlarmEvent.Level.RECOVERY,
                channel_type=LcdChannel.HIGH.value,
            )
        except Exception:
            logger.exception("Temperature alarm LCD notification failed")
            event.lcd_notified = False
        update_fields.append("lcd_notified")

    if thermometer.alarm_net_message_enabled:
        broadcast_kwargs = {}
        if thermometer.alarm_lcd_enabled:
            broadcast_kwargs["lcd_channel_type"] = LcdChannel.HIGH.value
        else:
            broadcast_kwargs["lcd_channel_type"] = NetMessage.SUPPRESS_LCD_CHANNEL_TYPE
        try:
            event.net_message = NetMessage.broadcast(
                subject,
                body,
                **broadcast_kwargs,
            )
            if thermometer.alarm_lcd_enabled:
                event.lcd_notified = True
                update_fields.append("lcd_notified")
        except Exception:
            logger.exception("Temperature alarm NetMessage broadcast failed")
            event.net_message = None
        update_fields.append("net_message")

    if update_fields:
        event.save(update_fields=update_fields)


def evaluate_temperature_alarm(
    thermometer: Thermometer,
    reading: Decimal | None,
    *,
    read_at: datetime | None = None,
) -> TemperatureAlarmResult:
    """Evaluate configured alarm thresholds for one thermometer reading."""

    if reading is None:
        return TemperatureAlarmResult(
            status="missing-reading",
            level=thermometer.last_alarm_level or Thermometer.AlarmLevel.NORMAL,
        )
    now = read_at or timezone.now()
    with transaction.atomic():
        thermometer = Thermometer.objects.select_for_update().get(pk=thermometer.pk)
        if not thermometer.alarm_enabled:
            return TemperatureAlarmResult(
                status="disabled",
                level=Thermometer.AlarmLevel.NORMAL,
            )
        previous_level = thermometer.last_alarm_level or Thermometer.AlarmLevel.NORMAL
        level = _alarm_level_for_reading(thermometer, reading)

        if level == Thermometer.AlarmLevel.NORMAL:
            if previous_level in {
                Thermometer.AlarmLevel.WARNING,
                Thermometer.AlarmLevel.CRITICAL,
            }:
                subject, body = _build_alarm_message(
                    thermometer=thermometer,
                    level=level,
                    reading=reading,
                    threshold=None,
                )
                event = ThermometerAlarmEvent.objects.create(
                    thermometer=thermometer,
                    level=ThermometerAlarmEvent.Level.RECOVERY,
                    reading=reading,
                    message=body,
                    created=now,
                )
                thermometer.last_alarm_level = Thermometer.AlarmLevel.NORMAL
                thermometer.last_alarm_at = now
                thermometer.save(update_fields=["last_alarm_level", "last_alarm_at"])
                result = TemperatureAlarmResult(
                    status="recovered",
                    level=level,
                    event=event,
                )
            else:
                return TemperatureAlarmResult(status="normal", level=level)
        elif _rate_limited(thermometer, level=level, now=now):
            return TemperatureAlarmResult(status="rate-limited", level=level)
        else:
            threshold = _threshold_for_level(thermometer, level)
            subject, body = _build_alarm_message(
                thermometer=thermometer,
                level=level,
                reading=reading,
                threshold=threshold,
            )
            event = ThermometerAlarmEvent.objects.create(
                thermometer=thermometer,
                level=_alarm_event_level(level),
                reading=reading,
                threshold=threshold,
                message=body,
                created=now,
            )
            thermometer.last_alarm_level = level
            thermometer.last_alarm_at = now
            thermometer.save(update_fields=["last_alarm_level", "last_alarm_at"])
            result = TemperatureAlarmResult(status="emitted", level=level, event=event)

    _deliver_alarm(
        thermometer=thermometer,
        event=event,
        subject=subject,
        body=body,
    )
    return result
