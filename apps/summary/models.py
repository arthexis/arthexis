from __future__ import annotations

from django.db import models
from django.utils import timezone


class LLMSummaryConfig(models.Model):
    """Configuration and cursor state for LCD log summaries."""

    slug = models.SlugField(unique=True, default="lcd-log-summary")
    display = models.CharField(max_length=120, default="LCD Log Summary")
    model_path = models.CharField(max_length=255, blank=True)
    model_command = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    installed_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    log_offsets = models.JSONField(default=dict, blank=True)
    last_prompt = models.TextField(blank=True)
    last_output = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "LLM Summary Config"
        verbose_name_plural = "LLM Summary Configs"

    def mark_installed(self) -> None:
        if self.installed_at is None:
            self.installed_at = timezone.now()

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display


__all__ = ["LLMSummaryConfig"]
