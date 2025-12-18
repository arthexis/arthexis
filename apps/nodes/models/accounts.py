from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.sigils.fields import SigilShortAutoField


def ssh_key_upload_path(instance: "SSHAccount", filename: str) -> str:
    node_identifier = instance.node_id or "unassigned"
    return f"ssh_accounts/{node_identifier}/{Path(filename).name}"


class SSHAccount(Entity):
    """SSH credentials that can be linked to a :class:`Node`."""

    node = models.ForeignKey(
        "nodes.Node", on_delete=models.CASCADE, related_name="ssh_accounts"
    )
    username = models.CharField(max_length=150)
    password = SigilShortAutoField(
        max_length=255,
        blank=True,
        help_text="Password for password-based authentication.",
    )
    private_key = models.FileField(
        upload_to=ssh_key_upload_path,
        blank=True,
        help_text="Optional private key for key-based authentication.",
    )
    public_key = models.FileField(
        upload_to=ssh_key_upload_path,
        blank=True,
        help_text="Optional public key for key-based authentication.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SSH Account"
        verbose_name_plural = "SSH Accounts"
        unique_together = ("node", "username")
        ordering = ("username", "pk")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.username}@{self.node}" if self.node_id else self.username

    def clean(self):
        super().clean()
        has_password = bool((self.password or "").strip())
        has_key = bool(self.private_key or self.public_key)
        if not has_password and not has_key:
            raise ValidationError(
                _("Provide a password or upload an SSH key for authentication."),
            )
