from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.groups.models import SecurityGroup
from apps.media.models import MediaFile
from apps.media.utils import ensure_media_bucket
from apps.nodes.models import NodeRole


class ModuleManager(models.Manager):
    def get_by_natural_key(self, path: str):
        return self.get(path=path)

    def for_role(self, role: NodeRole | None):
        role_filter = Q(roles__isnull=True)
        if role is not None:
            role_filter |= Q(roles=role)
        return self.filter(role_filter | Q(security_mode=Module.SECURITY_INCLUSIVE)).distinct()


class Module(Entity):
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
    favicon_media = models.ForeignKey(
        MediaFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="module_favicons",
        verbose_name=_("Favicon"),
    )
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
    roles = models.ManyToManyField(
        NodeRole,
        related_name="modules",
        blank=True,
        help_text="Leave blank to apply this module to all node roles.",
    )

    objects = ModuleManager()

    class Meta:
        verbose_name = _("Module")
        verbose_name_plural = _("Modules")
        db_table = "pages_module"
        constraints = [
            models.UniqueConstraint(fields=["path"], name="unique_module_path"),
        ]

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.path,)

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

    @staticmethod
    def normalize_path(path: str | None) -> str | None:
        if path is None:
            return None
        stripped = str(path).strip("/")
        return "/" if stripped == "" else f"/{stripped}/"

    def save(self, *args, **kwargs):
        base_path = self.path
        if not base_path:
            base = self.menu or getattr(self.application, "name", "module")
            base_path = slugify(base)
        self.path = self.normalize_path(base_path)
        super().save(*args, **kwargs)

    @property
    def favicon_file(self):
        if self.favicon_media and self.favicon_media.file:
            return self.favicon_media.file
        return None

    @property
    def favicon_url(self) -> str:
        file = self.favicon_file
        return file.url if file else ""


__all__ = ["Module", "ModuleManager"]

MODULE_FAVICON_BUCKET_SLUG = "modules-favicons"
MODULE_FAVICON_ALLOWED_PATTERNS = "\n".join(["*.png", "*.ico", "*.svg", "*.jpg", "*.jpeg"])


def get_module_favicon_bucket():
    return ensure_media_bucket(
        slug=MODULE_FAVICON_BUCKET_SLUG,
        name=_("Module Favicons"),
        allowed_patterns=MODULE_FAVICON_ALLOWED_PATTERNS,
        max_bytes=512 * 1024,
        expires_at=None,
    )
