"""Celery tasks for the pages application."""

from celery import shared_task

from apps.sites.maintenance import purge_view_history
from apps.tasks.tasks import create_user_story_github_issue, purge_leads


@shared_task(name="apps.sites.tasks.purge_view_history")
def purge_view_history_task(days: int = 15) -> int:
    """Purge stale view history entries from periodic maintenance."""

    return purge_view_history(days=days)


__all__ = [
    "create_user_story_github_issue",
    "purge_leads",
    "purge_view_history_task",
]
