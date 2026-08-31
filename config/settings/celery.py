"""Celery runtime and schedule settings."""

import os
from collections.abc import Iterable, Sequence
from datetime import timedelta

from celery.schedules import crontab

from apps.celery.utils import resolve_celery_shutdown_timeout
from apps.core.auto_upgrade import AUTO_UPGRADE_CADENCE_HOUR, AUTO_UPGRADE_TASK_PATH
from apps.sensors.constants import USB_LCD_STATUS_CELERY_TASK_NAME
from apps.summary.constants import LLM_SUMMARY_CELERY_TASK_NAME

from .apps import INSTALLED_APPS, _app_entry_aliases
from .base import NODE_ROLE
from .broker import resolve_celery_broker_url
from .i18n import TIME_ZONE
from .logging import LOGGING

BeatSchedule = dict[str, object]
BeatScheduleEntry = tuple[str | None, str, BeatSchedule]

CELERY_BROKER_URL = resolve_celery_broker_url(node_role=NODE_ROLE)
# Legacy alias retained for older deployments that still export BROKER_URL.
BROKER_URL = CELERY_BROKER_URL
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "cache+memory://")
# Keep Celery Beat schedules in memory to avoid database-backed scheduling
# (e.g., django-celery-beat), which can contend with migrations.
CELERY_BEAT_SCHEDULER = "celery.beat:Scheduler"
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
# Align Celery's log formatting with Django's logging configuration so worker
# output follows the same conventions without polluting the error log.
CELERY_WORKER_LOG_FORMAT = LOGGING["formatters"]["standard"]["format"]
CELERY_WORKER_TASK_LOG_FORMAT = LOGGING["formatters"]["standard"]["format"]
# Allow Celery workers extra time to finish acknowledged jobs before SIGTERM.
CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT = resolve_celery_shutdown_timeout()
# Legacy alias retained for fixture references and admin guidance.
CELERY_WORKER_SHUTDOWN_TIMEOUT = CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT

_CELERY_BEAT_SCHEDULE_ENTRIES: tuple[BeatScheduleEntry, ...] = (
    (
        "apps.nodes",
        "auto_upgrade_check",
        {
            "task": AUTO_UPGRADE_TASK_PATH,
            "schedule": crontab(minute=0, hour=AUTO_UPGRADE_CADENCE_HOUR),
        },
    ),
    (
        "apps.core",
        "heartbeat",
        {
            "task": "apps.core.tasks.heartbeat",
            "schedule": crontab(minute="*/5"),
        },
    ),
    (
        "apps.screens",
        "llm_summary_lcd",
        {
            "task": LLM_SUMMARY_CELERY_TASK_NAME,
            "schedule": timedelta(minutes=5),
        },
    ),
    (
        "apps.sensors",
        "thermometer_sampling",
        {
            "task": "apps.sensors.tasks.sample_thermometers",
            "schedule": timedelta(minutes=1),
        },
    ),
    (
        "apps.screens",
        "usb_lcd_status",
        {
            "task": USB_LCD_STATUS_CELERY_TASK_NAME,
            "schedule": timedelta(seconds=30),
        },
    ),
    (
        "apps.ocpp",
        "ocpp_configuration_check",
        {
            "task": "apps.ocpp.tasks.schedule_daily_charge_point_configuration_checks",
            "schedule": crontab(minute=0, hour=0),
        },
    ),
    (
        "apps.ocpp",
        "ocpp_firmware_snapshot",
        {
            "task": "apps.ocpp.tasks.schedule_daily_firmware_snapshot_requests",
            "schedule": crontab(minute=30, hour=0),
        },
    ),
    (
        "apps.ocpp",
        "ocpp_offline_notifications",
        {
            "task": "apps.ocpp.tasks.send_offline_charge_point_notifications",
            "schedule": timedelta(minutes=5),
        },
    ),
    (
        "apps.ocpp",
        "ocpp_meter_value_purge",
        {
            "task": "apps.ocpp.tasks.purge_meter_values",
            "schedule": crontab(minute=0, hour=3),
        },
    ),
    (
        "apps.ocpp",
        "ocpp_power_projection",
        {
            "task": "apps.ocpp.tasks.schedule_power_projection_requests",
            "schedule": crontab(minute=0, hour=1),
        },
    ),
    (
        "apps.certs",
        "certificate_expiration_refresh",
        {
            "task": "apps.certs.tasks.refresh_certificate_expirations",
            "schedule": crontab(minute=0, hour=2),
        },
    ),
    (
        "apps.sites",
        "site_view_history_purge",
        {
            "task": "apps.sites.tasks.purge_view_history",
            "schedule": crontab(minute=45, hour=3),
        },
    ),
    (
        "apps.core",
        "log_retention_guard",
        {
            "task": "apps.core.tasks.log_retention.enforce_log_retention",
            "schedule": crontab(minute=15, hour=4),
        },
    ),
    (
        "apps.repos",
        "github_monitor",
        {
            "task": "apps.repos.tasks.monitor_github_readiness",
            "schedule": crontab(minute="*/10"),
        },
    ),
    (
        "apps.repos",
        "repository_work_assignment_upstream_pull",
        {
            "task": "apps.repos.tasks.pull_upstream_repository_assignments",
            "schedule": timedelta(minutes=2),
        },
    ),
    (
        "apps.skills",
        "codex_token_budget_monitor",
        {
            "task": "apps.skills.tasks.monitor_codex_token_budgets",
            "schedule": timedelta(minutes=15),
        },
    ),
)


def _installed_app_aliases(installed_apps: Iterable[str]) -> set[str]:
    aliases: set[str] = set()
    for app_entry in installed_apps:
        aliases.update(_app_entry_aliases(app_entry))
    return aliases


def _resolve_celery_beat_schedule(
    entries: Sequence[BeatScheduleEntry] = _CELERY_BEAT_SCHEDULE_ENTRIES,
    *,
    installed_apps: Iterable[str] = INSTALLED_APPS,
) -> dict[str, BeatSchedule]:
    installed_aliases = _installed_app_aliases(installed_apps)
    schedule: dict[str, BeatSchedule] = {}
    for app_selector, schedule_name, beat_schedule in entries:
        if app_selector is None or app_selector in installed_aliases:
            schedule[schedule_name] = beat_schedule

    return schedule


CELERY_BEAT_SCHEDULE = _resolve_celery_beat_schedule()
