"""Archived extension records retained for migration-state compatibility."""

from __future__ import annotations

from django.db import models


class ArchivedJsExtension(models.Model):
    """Archived snapshot of a removed hosted JS extension record."""

    original_id = models.BigIntegerField(unique=True)
    is_seed_data = models.BooleanField(default=False, editable=False)
    is_user_data = models.BooleanField(default=False, editable=False)
    is_deleted = models.BooleanField(default=False, editable=False)
    slug = models.SlugField(max_length=100)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    version = models.CharField(default="0.1.0", max_length=50)
    manifest_version = models.PositiveSmallIntegerField(default=3)
    is_enabled = models.BooleanField(default=True)
    matches = models.TextField(blank=True)
    content_script = models.TextField(blank=True)
    background_script = models.TextField(blank=True)
    options_page = models.TextField(blank=True)
    permissions = models.TextField(blank=True)
    host_permissions = models.TextField(blank=True)
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(manifest_version__in=(2, 3)),
                name="extensions_archivedjsextension_manifest_version_valid",
            )
        ]
        ordering = ["slug", "original_id"]


__all__ = ["ArchivedJsExtension"]
