"""Models for SMB server and partition orchestration."""

from encrypted_model_fields.fields import EncryptedCharField
from django.db import models


class SMBServer(models.Model):
    """Connection details for a remote SMB host."""

    name = models.CharField(max_length=120, unique=True)
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=445)
    username = models.CharField(max_length=120, blank=True, default="")
    password = EncryptedCharField(max_length=255, blank=True, default="")
    domain = models.CharField(max_length=120, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "SMB Server"
        verbose_name_plural = "SMB Servers"

    def __str__(self) -> str:
        """Return a readable server label."""

        return f"{self.name} ({self.host}:{self.port})"


class SMBPartition(models.Model):
    """Represents an SMB-exported share bound to a local partition path."""

    server = models.ForeignKey(SMBServer, on_delete=models.CASCADE, related_name="partitions")
    name = models.CharField(max_length=120)
    share_name = models.CharField(max_length=120)
    local_path = models.CharField(max_length=255)
    device = models.CharField(max_length=64, blank=True, default="")
    filesystem = models.CharField(max_length=32, blank=True, default="")
    size_bytes = models.BigIntegerField(null=True, blank=True)
    mount_options = models.CharField(max_length=255, blank=True, default="rw")
    is_enabled = models.BooleanField(default=True)
    last_discovered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        unique_together = ("server", "share_name")
        verbose_name = "SMB Partition"
        verbose_name_plural = "SMB Partitions"

    def __str__(self) -> str:
        """Return a readable partition label."""

        return f"{self.name} -> //{self.server.host}/{self.share_name}"
