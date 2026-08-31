"""Celery tasks for the pages application."""

import logging

from celery import shared_task

from apps.sites.maintenance import purge_view_history

logger = logging.getLogger(__name__)


@shared_task(name="apps.sites.tasks.purge_view_history")
def purge_view_history_task(days: int = 15) -> int:
    """Purge stale view history entries from periodic maintenance."""

    return purge_view_history(days=days)


@shared_task(name="apps.sites.tasks.create_user_story_github_issue")
def create_user_story_github_issue(user_story_id: int) -> str | None:
    """Create a GitHub issue for the provided ``UserStory`` instance."""

    from apps.sites.models import UserStory

    try:
        story = UserStory.objects.get(pk=user_story_id)
    except UserStory.DoesNotExist:
        logger.warning(
            "User story %s no longer exists; skipping GitHub issue creation",
            user_story_id,
        )
        return None

    if story.github_issue_url:
        logger.info(
            "GitHub issue already recorded for user story %s: %s",
            story.pk,
            story.github_issue_url,
        )
        return story.github_issue_url

    issue_url = story.create_github_issue()

    if issue_url:
        logger.info("Created GitHub issue %s for user story %s", issue_url, story.pk)
    else:
        logger.info("No GitHub issue created for user story %s", story.pk)

    return issue_url


__all__ = [
    "create_user_story_github_issue",
    "purge_view_history_task",
]
