"""Archive-only survey models retained for migration-state consistency."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class ArchivedSurveyTopic(models.Model):
    """Archived snapshot of a historical survey topic.

    Parameters:
        None.

    Returns:
        None.
    """

    original_id = models.BigIntegerField(unique=True)
    is_seed_data = models.BooleanField(default=False, editable=False)
    is_user_data = models.BooleanField(default=False, editable=False)
    is_deleted = models.BooleanField(default=False, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField()
    description = models.TextField(blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "original_id"]


class ArchivedSurveyQuestion(models.Model):
    """Archived snapshot of a historical survey question.

    Parameters:
        None.

    Returns:
        None.
    """

    original_id = models.BigIntegerField(unique=True)
    topic_original_id = models.BigIntegerField()
    is_seed_data = models.BooleanField(default=False, editable=False)
    is_user_data = models.BooleanField(default=False, editable=False)
    is_deleted = models.BooleanField(default=False, editable=False)
    prompt = models.TextField()
    question_type = models.CharField(
        max_length=12,
        choices=[("binary", "Binary"), ("open", "Open ended")],
        default="binary",
    )
    yes_label = models.CharField(max_length=64, default="Yes")
    no_label = models.CharField(max_length=64, default="No")
    priority = models.IntegerField(default=0)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["topic_original_id", "-priority", "position", "original_id"]


class ArchivedSurveyResult(models.Model):
    """Archived snapshot of a historical survey result payload.

    Parameters:
        None.

    Returns:
        None.
    """

    original_id = models.BigIntegerField(unique=True)
    topic_original_id = models.BigIntegerField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    session_key = models.CharField(max_length=40, blank=True, default="")
    is_seed_data = models.BooleanField(default=False, editable=False)
    is_user_data = models.BooleanField(default=False, editable=False)
    is_deleted = models.BooleanField(default=False, editable=False)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "original_id"]


__all__ = [
    "ArchivedSurveyQuestion",
    "ArchivedSurveyResult",
    "ArchivedSurveyTopic",
]
