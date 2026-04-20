"""Spam assessment models for GitHub issues."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.repos.models.events import GitHubEvent
from apps.repos.models.repositories import GitHubRepository


class RepositoryIssueSpamAssessment(Entity):
    """Persist spam scoring outcomes for GitHub issue webhook events."""

    repository = models.ForeignKey(
        GitHubRepository,
        related_name="issue_spam_assessments",
        on_delete=models.CASCADE,
    )
    event = models.ForeignKey(
        GitHubEvent,
        related_name="issue_spam_assessments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    issue_number = models.PositiveIntegerField()
    issue_title = models.CharField(max_length=500, blank=True)
    issue_body = models.TextField(blank=True)
    issue_author = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=50, blank=True)
    score = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    is_spam = models.BooleanField(default=False)
    reasons = models.JSONField(default=list, blank=True)
    delivery_id = models.CharField(max_length=255, blank=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-processed_at",)
        verbose_name = _("Repository Issue Spam Assessment")
        verbose_name_plural = _("Repository Issue Spam Assessments")
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "issue_number", "delivery_id"],
                name="unique_issue_spam_assessment_delivery",
            )
        ]
        indexes = [
            models.Index(fields=["is_spam", "processed_at"], name="repo_issue_spam_idx"),
            models.Index(fields=["issue_author"], name="repo_issue_spam_author_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = "spam" if self.is_spam else "clean"
        return f"#{self.issue_number} {label} ({self.score})"
