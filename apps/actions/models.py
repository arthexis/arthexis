"""Models for bearer-token backed remote actions."""

from __future__ import annotations

import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import Ownable


class RemoteAction(Ownable):
    """Ownable action that maps an API operation to a recipe."""

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    display = models.CharField(max_length=120)
    slug = models.SlugField(max_length=100, unique=True)
    operation_id = models.CharField(
        max_length=120,
        unique=True,
        help_text=_("OpenAPI operationId for this action."),
    )
    description = models.TextField(blank=True)
    recipe = models.ForeignKey(
        "recipes.Recipe",
        on_delete=models.PROTECT,
        related_name="remote_actions",
        help_text=_("Recipe executed when this action is invoked."),
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display",)
        verbose_name = _("Remote Action")
        verbose_name_plural = _("Remote Actions")

    def __str__(self) -> str:
        """Return the human-readable action name."""

        return self.display


class RemoteActionToken(models.Model):
    """Bearer token used to authorize remote action calls for a user."""

    DEFAULT_EXPIRATION = timezone.timedelta(days=1)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="remote_action_tokens",
        help_text=_("User that owns this bearer token."),
    )
    label = models.CharField(max_length=100, blank=True)
    key_prefix = models.CharField(max_length=12, editable=False)
    key_hash = models.CharField(max_length=64, editable=False, unique=True)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("Remote Action Token")
        verbose_name_plural = _("Remote Action Tokens")

    def __str__(self) -> str:
        """Return a compact token identifier for admin screens."""

        return f"{self.user} ({self.key_prefix}…)"

    @classmethod
    def _build_hash(cls, raw_key: str) -> str:
        """Return a deterministic SHA-256 hash for a raw token key."""

        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def issue_for_user(
        cls,
        user,
        *,
        label: str = "",
        expires_at: timezone.datetime | None = None,
    ) -> tuple["RemoteActionToken", str]:
        """Create and return a new token plus its one-time raw bearer value."""

        raw_key = secrets.token_urlsafe(32)
        expiration = expires_at or (timezone.now() + cls.DEFAULT_EXPIRATION)
        token = cls.objects.create(
            user=user,
            label=label,
            key_prefix=raw_key[:12],
            key_hash=cls._build_hash(raw_key),
            expires_at=expiration,
        )
        return token, raw_key

    @property
    def is_expired(self) -> bool:
        """Return whether the token has already expired."""

        return timezone.now() >= self.expires_at

    @classmethod
    def authenticate_bearer(cls, bearer_value: str) -> "RemoteActionToken":
        """Resolve and validate a bearer token value.

        Raises:
            ValueError: When the token is malformed, missing, inactive, or expired.
        """

        candidate = (bearer_value or "").strip()
        if not candidate:
            raise ValueError("Missing bearer token.")

        token = cls.objects.filter(key_hash=cls._build_hash(candidate)).select_related("user").first()
        if token is None:
            raise ValueError("Invalid bearer token.")
        if not token.is_active:
            raise ValueError("Token is inactive.")
        if token.is_expired:
            raise ValueError("Token has expired.")

        now = timezone.localtime()
        if token.last_used_at is None or (now - token.last_used_at) > timezone.timedelta(minutes=1):
            token.last_used_at = now
            token.save(update_fields=["last_used_at"])
        return token
