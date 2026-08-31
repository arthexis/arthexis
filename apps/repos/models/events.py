"""Webhook event models for repositories."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.repos.models.repositories import GitHubRepository


class RepositoryEvent(Entity):
    """Abstract base for repository webhook events."""

    received_at = models.DateTimeField(auto_now_add=True)
    http_method = models.CharField(max_length=10, blank=True)
    headers = models.JSONField(default=dict, blank=True)
    query_params = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    raw_body = models.TextField(blank=True)

    class Meta:
        abstract = True


class GitHubEvent(RepositoryEvent):
    """Persist raw GitHub webhook payloads."""

    repository = models.ForeignKey(
        GitHubRepository,
        related_name="webhook_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    owner = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255, blank=True)
    event_type = models.CharField(max_length=255, blank=True)
    delivery_id = models.CharField(max_length=255, blank=True)
    hook_id = models.CharField(max_length=255, blank=True)
    signature = models.CharField(max_length=255, blank=True)
    signature_256 = models.CharField(max_length=255, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("-received_at",)
        verbose_name = _("GitHub Event")
        verbose_name_plural = _("GitHub Events")
        indexes = [
            models.Index(fields=["event_type"], name="github_event_type_idx"),
            models.Index(fields=["delivery_id"], name="github_delivery_id_idx"),
        ]

    def __str__(self):  # pragma: no cover - simple representation
        identity = self.repository or f"{self.owner}/{self.name}".strip("/")
        return f"{self.event_type or 'github'} event for {identity}".strip()
