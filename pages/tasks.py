"""Celery tasks for the pages application."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task

from django.utils import timezone


logger = logging.getLogger(__name__)


@shared_task
def create_user_story_github_issue(user_story_id: int) -> str | None:
    """Create a GitHub issue for the provided ``UserStory`` instance."""

    from .models import UserStory

    try:
        story = UserStory.objects.get(pk=user_story_id)
    except UserStory.DoesNotExist:  # pragma: no cover - defensive guard
        logger.warning(
            "User story %s no longer exists; skipping GitHub issue creation",
            user_story_id,
        )
        return None

    if story.rating >= 5:
        logger.info(
            "Skipping GitHub issue creation for user story %s with rating %s",
            story.pk,
            story.rating,
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
        logger.info(
            "Created GitHub issue %s for user story %s", issue_url, story.pk
        )
    else:
        logger.info(
            "No GitHub issue created for user story %s", story.pk
        )

    return issue_url


@shared_task
def purge_expired_landing_leads(days: int = 30) -> int:
    """Remove landing leads older than ``days`` days."""

    from .models import LandingLead

    cutoff = timezone.now() - timedelta(days=days)
    queryset = LandingLead.objects.filter(created_on__lt=cutoff)
    deleted, _ = queryset.delete()
    if deleted:
        logger.info(
            "Purged %s landing leads older than %s days", deleted, days
        )
    return deleted
