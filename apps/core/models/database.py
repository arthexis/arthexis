from __future__ import annotations

from django.db import models


class DatabaseConfig(models.Model):
    """Store database connection configuration for runtime health checks."""

    backend = models.CharField(max_length=32, default="postgres")
    name = models.CharField(max_length=255)
    user = models.CharField(max_length=255)
    host = models.CharField(max_length=255, default="localhost")
    port = models.PositiveIntegerField(default=5432)
    is_active = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_status_ok = models.BooleanField(default=False)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Database configuration"
        verbose_name_plural = "Database configurations"
        ordering = ["-updated_at"]

    def __str__(self) -> str:  # pragma: no cover - representational only
        return f"{self.backend}://{self.user}@{self.host}:{self.port}/{self.name}"
