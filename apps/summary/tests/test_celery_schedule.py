from datetime import timedelta

from celery import current_app
from django.conf import settings

from apps.summary.constants import LLM_SUMMARY_CELERY_TASK_NAME


def test_llm_summary_lcd_static_schedule_and_registered_task_name() -> None:
    """The live beat service uses the static scheduler, not DB-backed beat rows."""

    entry = settings.CELERY_BEAT_SCHEDULE["llm_summary_lcd"]

    assert entry["task"] == LLM_SUMMARY_CELERY_TASK_NAME
    assert entry["schedule"] == timedelta(minutes=5)
    from apps.summary import tasks as _summary_tasks

    del _summary_tasks

    registered_task_names = set(current_app.tasks.keys())

    assert LLM_SUMMARY_CELERY_TASK_NAME in registered_task_names
    assert "summary.tasks.generate_lcd_log_summary" not in registered_task_names
