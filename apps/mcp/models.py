from __future__ import annotations

"""Models backing MCP API key authentication."""

import hashlib
import secrets
from dataclasses import dataclass

from django.conf import settings
from django.db import models
from django.utils import timezone


@dataclass(frozen=True)
class GeneratedMcpApiKey:
    """Container for a newly generated API key and its persisted digest."""

    plain_key: str
    key_prefix: str
    key_hash: str


class McpApiKeyQuerySet(models.QuerySet["McpApiKey"]):
    """Query helpers for MCP API keys."""

    def active(self) -> "McpApiKeyQuerySet":
        """Return API keys that are currently valid for authentication."""

        now = timezone.now()
        return self.filter(revoked_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )


class McpApiKeyManager(models.Manager["McpApiKey"]):
    """Manager utilities for key generation and authentication."""

    def get_queryset(self) -> McpApiKeyQuerySet:
        """Return the typed MCP API key queryset."""

        return McpApiKeyQuerySet(self.model, using=self._db)

    @staticmethod
    def generate_key() -> GeneratedMcpApiKey:
        """Generate a random API key and its deterministic hash metadata."""

        token = secrets.token_urlsafe(36)
        plain_key = f"mcp_{token}"
        key_hash = hashlib.sha256(plain_key.encode("utf-8")).hexdigest()
        return GeneratedMcpApiKey(
            plain_key=plain_key,
            key_prefix=plain_key[:12],
            key_hash=key_hash,
        )

    def create_for_user(
        self,
        *,
        user: settings.AUTH_USER_MODEL,
        label: str,
        expires_at=None,
    ) -> tuple["McpApiKey", str]:
        """Create and persist a key for a user, returning model + plain key."""

        generated = self.generate_key()
        api_key = self.create(
            user=user,
            label=label,
            expires_at=expires_at,
            key_prefix=generated.key_prefix,
            key_hash=generated.key_hash,
        )
        return api_key, generated.plain_key

    def authenticate_key(self, plain_key: str) -> "McpApiKey | None":
        """Resolve and return the active key record that matches ``plain_key``."""

        if not isinstance(plain_key, str) or not plain_key.strip():
            return None

        key_hash = hashlib.sha256(plain_key.encode("utf-8")).hexdigest()
        return (
            self.get_queryset()
            .active()
            .select_related("user")
            .filter(key_hash=key_hash)
            .first()
        )


class McpApiKey(models.Model):
    """Hash-based API key used to authenticate MCP requests."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_api_keys",
    )
    label = models.CharField(max_length=120)
    key_prefix = models.CharField(max_length=20, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = McpApiKeyManager()

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "MCP API key"
        verbose_name_plural = "MCP API keys"

    def __str__(self) -> str:
        """Return a concise description suitable for admin lists."""

        return f"{self.user} [{self.label}]"

    @property
    def is_active(self) -> bool:
        """Return whether this key can currently be used for authentication."""

        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        return self.expires_at > timezone.now()

    def mark_used(self) -> None:
        """Track key usage timestamp."""

        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])

    def revoke(self) -> None:
        """Invalidate this key immediately."""

        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at"])
