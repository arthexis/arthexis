from __future__ import annotations

from django.db import models


class SummaryState(models.Model):
    """Track log offsets and timing for LCD summaries."""

    slug = models.SlugField(max_length=50, unique=True, default="default")
    last_run_at = models.DateTimeField(null=True, blank=True)
    log_offsets = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Summary State"
        verbose_name_plural = "Summary States"

    @classmethod
    def get_default(cls) -> "SummaryState":
        state, _ = cls.objects.get_or_create(slug="default")
        return state
