from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity

from apps.modules.models import Module


class LandingManager(models.Manager):
    def get_by_natural_key(self, module_path: str, path: str):
        return self.get(module__path=module_path, path=path)


class Landing(Entity):
    module = models.ForeignKey(
        Module, on_delete=models.CASCADE, related_name="landings"
    )
    path = models.CharField(max_length=200)
    label = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    track_leads = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    validation_status = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Validation Status",
        help_text="HTTP status code from the last landing validation attempt.",
    )
    validated_url_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Validated URL",
        help_text="Timestamp of the last landing validation check.",
    )

    objects = LandingManager()

    class Meta:
        unique_together = ("module", "path")
        verbose_name = _("Landing")
        verbose_name_plural = _("Landings")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.label} ({self.path})"

    def is_link_valid(self) -> bool:
        """Return ``True`` when the landing link is considered valid."""

        if self.validation_status is None:
            return True
        return 200 <= self.validation_status < 400

    def save(self, *args, **kwargs):
        existing = None
        if not self.pk:
            existing = type(self).objects.filter(module=self.module, path=self.path).first()
        if existing:
            self.pk = existing.pk
        super().save(*args, **kwargs)
