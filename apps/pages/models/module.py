from __future__ import annotations
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.groups.models import SecurityGroup
from apps.nodes.models import NodeRole


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
        "app.Application",
        on_delete=models.SET_NULL,
        related_name="modules",
        null=True,
        blank=True,
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
    security_group = models.ForeignKey(
        SecurityGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modules",
    )
    SECURITY_EXCLUSIVE = "exclusive"
    SECURITY_INCLUSIVE = "inclusive"
    SECURITY_CHOICES = (
        (SECURITY_EXCLUSIVE, "Exclusive"),
        (SECURITY_INCLUSIVE, "Inclusive"),
    )
    security_mode = models.CharField(
        max_length=10,
        choices=SECURITY_CHOICES,
        default=SECURITY_INCLUSIVE,
        help_text="Exclusive requires site and group match; inclusive allows either.",
    )

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
        label = self.menu_label or "Module"
        return f"{label} ({self.path})"

    @property
    def menu_label(self) -> str:
        if self.menu:
            return self.menu
        if self.application:
            return self.application.name
        return (self.path or "").strip("/") or "Module"

    def save(self, *args, **kwargs):
        if not self.path:
            base = self.menu or getattr(self.application, "name", "module")
            self.path = f"/{slugify(base)}/"
        super().save(*args, **kwargs)

