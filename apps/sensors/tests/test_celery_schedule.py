from datetime import timedelta

from celery import current_app
from django.conf import settings

from apps.sensors.constants import USB_LCD_STATUS_CELERY_TASK_NAME


def test_usb_lcd_status_static_schedule_and_registered_task_name() -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["usb_lcd_status"]

    assert entry["task"] == USB_LCD_STATUS_CELERY_TASK_NAME
    assert entry["schedule"] == timedelta(seconds=30)
    from apps.sensors import tasks as _sensor_tasks

    del _sensor_tasks

    assert USB_LCD_STATUS_CELERY_TASK_NAME in set(current_app.tasks.keys())
