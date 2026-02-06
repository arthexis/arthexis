from __future__ import annotations

from pathlib import Path
import logging

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.celery.utils import is_celery_enabled
from apps.screens.startup_notifications import lcd_feature_enabled

logger = logging.getLogger(__name__)


class LifecycleService(models.Model):
    class Activation(models.TextChoices):
        ALWAYS = "always", _("Always enabled")
        FEATURE = "feature", _("Node feature")
        LOCKFILE = "lockfile", _("Lock file")
        MANUAL = "manual", _("Manual")

    slug = models.SlugField(max_length=64, unique=True)
    display = models.CharField(max_length=80)
    unit_template = models.CharField(
        max_length=120,
        help_text=_('Systemd unit template, for example "celery-{service}.service".'),
    )
    pid_file = models.CharField(max_length=120, blank=True)
    docs_path = models.CharField(max_length=160, blank=True)
    activation = models.CharField(
        max_length=16,
        choices=Activation.choices,
        default=Activation.MANUAL,
    )
    feature_slug = models.SlugField(max_length=64, blank=True)
    lock_names = models.JSONField(default=list, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "display"]
        verbose_name = "Lifecycle Service"
        verbose_name_plural = "Lifecycle Services"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display

    def uses_service_name(self) -> bool:
        """Return True when the unit template uses a service placeholder."""
        return "{service}" in self.unit_template

    def _safe_lock_names(self) -> list[str]:
        """Return lock file names that are safe to resolve within the lock directory."""
        safe_names: list[str] = []
        for name in self.lock_names or []:
            if not isinstance(name, str):
                continue
            normalized = name.strip()
            if not normalized:
                continue
            candidate = Path(normalized)
            if candidate.is_absolute():
                continue
            if candidate.name != normalized:
                continue
            if ".." in candidate.parts:
                continue
            safe_names.append(normalized)
        return safe_names

    def _lockfile_enabled(self, lock_dir: Path) -> bool:
        """Return True when a configured lock file enables the service."""
        lock_names = self._safe_lock_names()
        if not lock_names or not lock_dir:
            return False
        if "celery.lck" in lock_names:
            return is_celery_enabled(lock_dir / "celery.lck")
        if "lcd_screen.lck" in lock_names:
            return lcd_feature_enabled(lock_dir)
        return any((lock_dir / name).exists() for name in lock_names)

    def is_configured(
        self,
        *,
        service_name: str | None,
        lock_dir: Path,
    ) -> bool:
        """Return True when this service should be configured for the node."""
        if not service_name and self.uses_service_name():
            return False
        if self.activation == self.Activation.ALWAYS:
            return True
        if self.activation == self.Activation.MANUAL:
            return False
        if self.activation == self.Activation.FEATURE:
            if not self.feature_slug:
                return False
            try:
                from apps.nodes.models import NodeFeature
            except ImportError:
                logger.debug("Unable to import NodeFeature", exc_info=True)
                return False
            feature = NodeFeature.objects.filter(slug=self.feature_slug).first()
            return bool(feature and feature.is_enabled)
        if self.activation == self.Activation.LOCKFILE:
            return self._lockfile_enabled(lock_dir)
        return False
