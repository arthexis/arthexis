from datetime import timedelta

from celery import current_app
from django.conf import settings

from apps.summary.constants import LLM_SUMMARY_CELERY_TASK_NAME
from config.settings.celery import _resolve_celery_beat_schedule


def test_llm_summary_lcd_screens_schedule_and_registered_task_name() -> None:
    """The live beat service uses the static scheduler, not DB-backed beat rows."""

    schedule = _resolve_celery_beat_schedule(
        installed_apps=[*settings.INSTALLED_APPS, "apps.screens"],
    )
    entry = schedule["llm_summary_lcd"]

    assert entry["task"] == LLM_SUMMARY_CELERY_TASK_NAME
    assert entry["schedule"] == timedelta(minutes=5)
    assert "llm_summary_lcd" not in settings.CELERY_BEAT_SCHEDULE

    from apps.summary import tasks as _summary_tasks

    del _summary_tasks

    registered_task_names = set(current_app.tasks.keys())

    assert LLM_SUMMARY_CELERY_TASK_NAME in registered_task_names
    assert "summary.tasks.generate_lcd_log_summary" not in registered_task_names
