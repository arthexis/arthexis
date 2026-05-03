from datetime import timedelta

from django.conf import settings

from apps.summary.constants import LLM_SUMMARY_CELERY_TASK_NAME


def test_llm_summary_lcd_uses_static_beat_schedule() -> None:
    """The live beat service uses the static scheduler, not DB-backed beat rows."""

    entry = settings.CELERY_BEAT_SCHEDULE["llm_summary_lcd"]

    assert entry["task"] == LLM_SUMMARY_CELERY_TASK_NAME
    assert entry["schedule"] == timedelta(minutes=5)
