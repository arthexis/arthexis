from __future__ import annotations

import logging
from importlib import import_module
from typing import TYPE_CHECKING

from django.db import models
from django.urls import URLPattern
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.app.models import Application
from apps.core.entity import Entity
from apps.nodes.models import NodeRole

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from .landing import Landing

logger = logging.getLogger(__name__)


class ModuleManager(models.Manager):
    def get_by_natural_key(self, role: str, path: str):
        return self.get(node_role__name=role, path=path)


class Module(Entity):
    node_role = models.ForeignKey(
        NodeRole,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    path = models.CharField(
        max_length=100,
        help_text="Base path for the app, starting with /",
        blank=True,
    )
    menu = models.CharField(
        max_length=100,
        blank=True,
        help_text="Text used for the navbar pill; defaults to the application name.",
    )
    priority = models.PositiveIntegerField(
        default=0,
        help_text="Lower values appear first in navigation pills.",
    )
    is_default = models.BooleanField(default=False)
    favicon = models.ImageField(upload_to="modules/favicons/", blank=True)

    objects = ModuleManager()

    class Meta:
        verbose_name = _("Module")
        verbose_name_plural = _("Modules")
        unique_together = ("node_role", "path")

    def natural_key(self):  # pragma: no cover - simple representation
        role_name = None
        if getattr(self, "node_role_id", None):
            role_name = self.node_role.name
        return (role_name, self.path)

    natural_key.dependencies = ["nodes.NodeRole"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.application.name} ({self.path})"

    @property
    def menu_label(self) -> str:
        return self.menu or self.application.name

    def save(self, *args, **kwargs):
        if not self.path:
            self.path = f"/{slugify(self.application.name)}/"
        super().save(*args, **kwargs)

    def _iter_landing_patterns(self, patterns, prefix=""):
        for pattern in patterns:
            if isinstance(pattern, URLPattern):
                yield prefix, pattern
            else:
                yield from self._iter_landing_patterns(
                    pattern.url_patterns, prefix=f"{prefix}{str(pattern.pattern)}"
                )

    def create_landings(self):
        try:
            urlconf = import_module(f"{self.application.name}.urls")
        except Exception:
            try:
                urlconf = import_module(f"{self.application.name.lower()}.urls")
            except Exception:
                from .landing import Landing

                Landing.objects.get_or_create(
                    module=self,
                    path=self.path,
                    defaults={"label": self.application.name},
                )
                return

        patterns = getattr(urlconf, "urlpatterns", [])
        created = False
        normalized_module = self.path.strip("/")

        from .landing import Landing

        for prefix, pattern in self._iter_landing_patterns(patterns):
            callback = pattern.callback
            if not getattr(callback, "landing", False):
                continue

            pattern_path = str(pattern.pattern)
            relative = f"{prefix}{pattern_path}"
            if normalized_module and relative.startswith(normalized_module):
                full_path = f"/{relative}"
            else:
                full_path = f"{self.path}{relative}"

            defaults = {
                "label": getattr(
                    callback,
                    "landing_label",
                    callback.__name__.replace("_", " ").title(),
                )
            }
            Landing.objects.update_or_create(
                module=self,
                path=full_path,
                defaults=defaults,
            )
            created = True

        if not created:
            Landing.objects.get_or_create(
                module=self, path=self.path, defaults={"label": self.application.name}
            )

    def should_create_landings(self, *, created: bool, raw: bool) -> bool:
        return created and not raw

    def handle_post_save(self, *, created: bool, raw: bool) -> None:
        if self.should_create_landings(created=created, raw=raw):
            self.create_landings()
