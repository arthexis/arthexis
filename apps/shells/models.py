"""Models for tracking shell scripts in the repository."""

from __future__ import annotations

from pathlib import Path

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.app.models import Application
from apps.core.entity import Entity


class AbstractShellScript(Entity):
    """Abstract base model shared by all shell script inventory records."""

    name = models.CharField(max_length=255)
    path = models.CharField(max_length=500, unique=True)

    class Meta:
        abstract = True
        ordering = ("path",)

    def __str__(self) -> str:
        """Return a human readable script identifier."""

        return self.path

    @property
    def repo_path(self) -> Path:
        """Return the script path as a ``Path`` instance."""

        return Path(self.path)


class BaseShellScript(AbstractShellScript):
    """Represent shell scripts located at the repository base path."""

    class Meta(AbstractShellScript.Meta):
        verbose_name = _("Base shell script")
        verbose_name_plural = _("Base shell scripts")


class AppShellScript(AbstractShellScript):
    """Represent shell scripts managed by an owning application."""

    managed_by = models.ForeignKey(
        Application,
        on_delete=models.PROTECT,
        related_name="app_shell_scripts",
        help_text=_("App responsible for this script."),
    )

    class Meta(AbstractShellScript.Meta):
        verbose_name = _("App shell script")
        verbose_name_plural = _("App shell scripts")
