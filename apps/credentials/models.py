from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.media.models import MediaFile
from apps.media.utils import ensure_media_bucket
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
    private_key_media = models.ForeignKey(
        MediaFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ssh_private_keys",
        verbose_name=_("Private key"),
        help_text="Optional private key for key-based authentication.",
    )
    public_key_media = models.ForeignKey(
        MediaFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ssh_public_keys",
        verbose_name=_("Public key"),
        help_text="Optional public key for key-based authentication.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SSH Account"
        verbose_name_plural = "SSH Accounts"
        unique_together = ("node", "username")
        ordering = ("username", "pk")
        db_table = "nodes_sshaccount"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.username}@{self.node}" if self.node_id else self.username

    def clean(self):
        super().clean()
        has_password = bool((self.password or "").strip())
        has_key = bool(self.private_key_media_id or self.public_key_media_id)
        if not has_password and not has_key:
            raise ValidationError(
                _("Provide a password or upload an SSH key for authentication."),
            )

    @property
    def private_key_file(self):
        if self.private_key_media and self.private_key_media.file:
            return self.private_key_media.file
        return None

    @property
    def public_key_file(self):
        if self.public_key_media and self.public_key_media.file:
            return self.public_key_media.file
        return None


SSH_KEY_BUCKET_SLUG = "credentials-ssh-keys"
SSH_KEY_ALLOWED_PATTERNS = "\n".join(["id_*", "*.pem", "*.pub", "*.key", "*.ppk"])


def get_ssh_key_bucket():
    return ensure_media_bucket(
        slug=SSH_KEY_BUCKET_SLUG,
        name=_("SSH Keys"),
        allowed_patterns=SSH_KEY_ALLOWED_PATTERNS,
        max_bytes=128 * 1024,
        expires_at=None,
    )
