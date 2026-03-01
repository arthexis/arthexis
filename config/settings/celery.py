"""Celery runtime and periodic schedule settings."""

import os
from datetime import timedelta

from celery.schedules import crontab

from apps.celery.utils import resolve_celery_shutdown_timeout

from .i18n import TIME_ZONE
from .logging import LOGGING

_ENV_CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "").strip()
CELERY_BROKER_URL = _ENV_CELERY_BROKER_URL or "memory://localhost/"
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "cache+memory://")
CELERY_BEAT_SCHEDULER = "celery.beat:Scheduler"
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_WORKER_LOG_FORMAT = LOGGING["formatters"]["standard"]["format"]
CELERY_WORKER_TASK_LOG_FORMAT = LOGGING["formatters"]["standard"]["format"]
CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT = resolve_celery_shutdown_timeout()
CELERY_WORKER_SHUTDOWN_TIMEOUT = CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT

CELERY_BEAT_SCHEDULE = {
    "heartbeat": {"task": "apps.core.tasks.heartbeat", "schedule": crontab(minute="*/5")},
    "ocpp_configuration_check": {
        "task": "apps.ocpp.tasks.schedule_daily_charge_point_configuration_checks",
        "schedule": crontab(minute=0, hour=0),
    },
    "ocpp_firmware_snapshot": {
        "task": "apps.ocpp.tasks.schedule_daily_firmware_snapshot_requests",
        "schedule": crontab(minute=30, hour=0),
    },
    "ocpp_forwarding_push": {"task": "apps.ocpp.tasks.setup_forwarders", "schedule": timedelta(minutes=5)},
    "ocpp_offline_notifications": {
        "task": "apps.ocpp.tasks.send_offline_charge_point_notifications",
        "schedule": timedelta(minutes=5),
    },
    "ocpp_meter_value_purge": {
        "task": "apps.ocpp.tasks.purge_meter_values",
        "schedule": crontab(minute=0, hour=3),
    },
    "ocpp_power_projection": {
        "task": "apps.ocpp.tasks.schedule_power_projection_requests",
        "schedule": crontab(minute=0, hour=1),
    },
    "web_request_sampling": {
        "task": "apps.content.tasks.run_scheduled_web_samplers",
        "schedule": timedelta(minutes=30),
    },
    "certificate_expiration_refresh": {
        "task": "apps.certs.tasks.refresh_certificate_expirations",
        "schedule": crontab(minute=0, hour=2),
    },
    "google_calendar_snapshot_sync": {
        "task": "apps.calendars.tasks.sync_google_calendars",
        "schedule": timedelta(hours=1),
    },
    "google_calendar_trigger_runner": {
        "task": "apps.calendars.tasks.run_calendar_event_triggers",
        "schedule": timedelta(minutes=15),
    },
}
