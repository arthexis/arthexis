"""Archived prompt records retained for migration-state compatibility."""

from __future__ import annotations

from django.db import models


class ArchivedStoredPrompt(models.Model):
    """Archived snapshot of a historical stored prompt record."""

    original_id = models.BigIntegerField(unique=True)
    is_seed_data = models.BooleanField(default=False, editable=False)
    is_user_data = models.BooleanField(default=False, editable=False)
    is_deleted = models.BooleanField(default=False, editable=False)
    slug = models.SlugField(max_length=120)
    title = models.CharField(max_length=200)
    prompt_text = models.TextField()
    initial_plan = models.TextField()
    change_reference = models.CharField(max_length=120, blank=True, default="")
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["slug", "original_id"]


__all__ = ["ArchivedStoredPrompt"]
