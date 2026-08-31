from datetime import timedelta

from celery import current_app
from django.conf import settings

from apps.sensors.constants import USB_LCD_STATUS_CELERY_TASK_NAME
from config.settings.celery import _resolve_celery_beat_schedule


def test_usb_lcd_status_control_schedule_and_registered_task_name() -> None:
    schedule = _resolve_celery_beat_schedule(
        installed_apps=[*settings.INSTALLED_APPS, "apps.screens"],
    )
    entry = schedule["usb_lcd_status"]

    assert entry["task"] == USB_LCD_STATUS_CELERY_TASK_NAME
    assert entry["schedule"] == timedelta(seconds=30)
    assert "usb_lcd_status" not in settings.CELERY_BEAT_SCHEDULE

    from apps.sensors import tasks as _sensor_tasks

    del _sensor_tasks

    assert USB_LCD_STATUS_CELERY_TASK_NAME in set(current_app.tasks.keys())
