from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class ViewHistory(Entity):
    """Record of public site visits."""

    path = models.CharField(max_length=500)
    method = models.CharField(max_length=10)
    status_code = models.PositiveSmallIntegerField()
    status_text = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    view_name = models.CharField(max_length=200, blank=True)
    visited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-visited_at"]
        verbose_name = _("View History")
        verbose_name_plural = _("View Histories")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.method} {self.path} ({self.status_code})"

    @classmethod
    def purge_older_than(cls, *, days: int) -> int:
        """Delete history entries recorded more than ``days`` days ago."""

        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = cls.objects.filter(visited_at__lt=cutoff).delete()
        return deleted
