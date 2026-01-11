from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.core.models import Ownable


class FTPServer(Entity):
    """Configuration for an embedded FTP server running on a node."""

    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="ftp_servers",
        null=True,
        blank=True,
        help_text=_("Node that should expose this FTP server configuration."),
    )
    bind_address = models.GenericIPAddressField(
        protocol="both",
        unpack_ipv4=True,
        default="0.0.0.0",
        help_text=_("Interface to bind when starting the FTP server."),
    )
    port = models.PositiveIntegerField(
        default=2121,
        help_text=_("TCP port where the FTP server listens."),
    )
    passive_ports = models.CharField(
        max_length=50,
        blank=True,
        help_text=_("Optional passive port range, e.g. 60000-60010."),
    )
    enabled = models.BooleanField(
        default=False,
        help_text=_("Toggle whether the FTP server should start."),
    )

    class Meta:
        verbose_name = "FTP Server"
        verbose_name_plural = "FTP Servers"
        constraints = [
            models.UniqueConstraint(
                fields=["node"],
                name="ftpserver_unique_node",
                condition=models.Q(node__isnull=False),
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = f"{self.bind_address}:{self.port}"
        if self.node_id:
            return f"{self.node} ({label})"
        return label

    def resolved_passive_ports(self) -> tuple[int, int] | None:
        if not self.passive_ports:
            return None
        if "-" not in self.passive_ports:
            return None
        start, end = self.passive_ports.split("-", 1)
        try:
            return int(start), int(end)
        except ValueError:
            return None


class FTPFolder(Ownable):
    """Folder exposed via the embedded FTP server."""

    owner_required = False

    class Permission(models.TextChoices):
        READ_ONLY = "read", _("Read-only")
        READ_WRITE = "write", _("Read and write")
        FULL_CONTROL = "admin", _("Full control")

        def to_ftp_rights(self) -> str:
            mapping = {
                FTPFolder.Permission.READ_ONLY: "elr",
                FTPFolder.Permission.READ_WRITE: "elradfmw",
                FTPFolder.Permission.FULL_CONTROL: "elradfmwMT",
            }
            return mapping.get(self, "elr")

    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="ftp_folders",
        null=True,
        blank=True,
        help_text=_("Node that owns this folder. Leave blank to share globally."),
    )
    name = models.CharField(max_length=100)
    path = models.CharField(max_length=500)
    enabled = models.BooleanField(default=False)
    owner_permission = models.CharField(
        max_length=10,
        choices=Permission.choices,
        default=Permission.FULL_CONTROL,
        help_text=_("Permission granted to the owner for this folder."),
    )
    group_permission = models.CharField(
        max_length=10,
        choices=Permission.choices,
        default=Permission.READ_ONLY,
        help_text=_("Permission granted to members of the security group."),
    )

    class Meta:
        verbose_name = "FTP Folder"
        verbose_name_plural = "FTP Folders"
        unique_together = ["node", "name"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def resolved_path(self) -> Path:
        candidate = Path(self.path)
        if not candidate.is_absolute():
            candidate = Path(settings.BASE_DIR) / candidate
        return candidate

    def build_link_name(self) -> str:
        base = slugify(self.name) or "folder"
        return f"{base}-{self.pk}" if self.pk else base
