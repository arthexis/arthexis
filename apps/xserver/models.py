from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from .utils import detect_x_server


class XDisplayInstance(Entity):
    """Detected X display server details bound to a node."""

    class RuntimeScope(models.TextChoices):
        """Describe where the display endpoint is hosted."""

        LOCAL = "local", _("Local")
        REMOTE = "remote", _("Remote")

    class ServerType(models.TextChoices):
        """Known X server implementations/types."""

        X11 = "x11", "X11"
        XORG = "xorg", "Xorg"
        XWAYLAND = "xwayland", "Xwayland"
        XEPHYR = "xephyr", "Xephyr"
        XVFB = "xvfb", "Xvfb"

    node = models.ForeignKey(
        "nodes.Node", on_delete=models.CASCADE, related_name="x_display_instances"
    )
    display_name = models.CharField(max_length=64)
    host = models.CharField(max_length=255, blank=True)
    runtime_scope = models.CharField(
        max_length=10,
        choices=RuntimeScope.choices,
        default=RuntimeScope.LOCAL,
    )
    server_type = models.CharField(
        max_length=16,
        choices=ServerType.choices,
        default=ServerType.X11,
    )
    process_name = models.CharField(max_length=64, blank=True)
    raw_data = models.JSONField(null=True, blank=True)
    detected_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name", "host"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "display_name"],
                name="xserver_display_instance_unique",
            )
        ]
        verbose_name = _("X Display Instance")
        verbose_name_plural = _("X Display Instances")

    def __str__(self) -> str:  # pragma: no cover - display helper
        """Return readable display label."""

        return f"{self.display_name} ({self.server_type})"

    @classmethod
    def refresh_from_system(cls, *, node) -> tuple[int, int]:
        """Detect and upsert local X display metadata for ``node``."""

        detected = detect_x_server()
        if detected is None:
            cls.objects.filter(node=node).delete()
            return 0, 0

        defaults = {
            "host": detected.host,
            "runtime_scope": detected.runtime_scope,
            "server_type": detected.server_type,
            "process_name": detected.process_name,
            "raw_data": detected.raw_data,
        }
        instance, created = cls.objects.update_or_create(
            node=node,
            display_name=detected.display_name,
            defaults=defaults,
        )
        cls.objects.filter(node=node).exclude(pk=instance.pk).delete()
        return (1, 0) if created else (0, 1)
