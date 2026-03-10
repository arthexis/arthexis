"""Models for bearer-token backed remote actions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import NoReverseMatch, reverse
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
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(user__isnull=True, group__isnull=True)
                    | models.Q(user__isnull=False, group__isnull=True)
                    | models.Q(user__isnull=True, group__isnull=False)
                ),
                name="actions_remoteaction_owner_exclusive",
            )
        ]

    def __str__(self) -> str:
        """Return the human-readable action name."""

        return self.display


class DashboardAction(models.Model):
    """Declarative dashboard action rendered on an admin model row."""

    class HttpMethod(models.TextChoices):
        """Supported methods used by dashboard model-row actions."""

        GET = "get", _("GET")
        POST = "post", _("POST")

    class TargetType(models.TextChoices):
        """Supported executable targets for dashboard actions."""

        ADMIN_URL = "admin_url", _("Admin URL Name")
        ABSOLUTE_URL = "absolute_url", _("Absolute URL")
        RECIPE = "recipe", _("Recipe")

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="dashboard_actions",
        help_text=_("Model row where this action appears on the admin dashboard."),
    )
    slug = models.SlugField(max_length=100)
    label = models.CharField(max_length=120)
    http_method = models.CharField(
        max_length=8,
        choices=HttpMethod.choices,
        default=HttpMethod.GET,
    )
    target_type = models.CharField(
        max_length=24,
        choices=TargetType.choices,
        default=TargetType.ADMIN_URL,
    )
    admin_url_name = models.CharField(max_length=200, blank=True)
    absolute_url = models.CharField(max_length=500, blank=True)
    recipe = models.ForeignKey(
        "recipes.Recipe",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="dashboard_actions",
    )
    caller_sigil = models.CharField(
        max_length=120,
        blank=True,
        help_text=_("Optional marker passed to downstream executables."),
    )
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "label")
        constraints = [
            models.UniqueConstraint(
                fields=("content_type", "slug"),
                name="actions_dashboardaction_unique_slug_per_model",
            )
        ]
        verbose_name = _("Dashboard Action")
        verbose_name_plural = _("Dashboard Actions")

    def __str__(self) -> str:
        """Return the UI label used for this dashboard action."""

        return self.label

    def clean(self):
        """Validate target fields for the selected target type."""

        super().clean()
        if self.target_type == self.TargetType.ADMIN_URL and not self.admin_url_name:
            raise ValidationError({"admin_url_name": _("Admin URL name is required.")})
        if self.target_type == self.TargetType.ABSOLUTE_URL and not self.absolute_url:
            raise ValidationError({"absolute_url": _("Absolute URL is required.")})
        if self.target_type == self.TargetType.ABSOLUTE_URL:
            parsed = urlparse((self.absolute_url or "").strip())
            is_safe_relative = bool(parsed.path) and not parsed.scheme and not parsed.netloc
            is_safe_absolute = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
            if not (is_safe_relative or is_safe_absolute):
                raise ValidationError({"absolute_url": _("Invalid or unsafe URL scheme.")})
        if self.target_type == self.TargetType.RECIPE and not self.recipe_id:
            raise ValidationError({"recipe": _("Recipe is required.")})
        if self.target_type == self.TargetType.RECIPE and self.http_method != self.HttpMethod.POST:
            raise ValidationError({"http_method": _("Recipe-backed actions must use POST.")})
        if self.caller_sigil and not self._is_safe_caller_sigil(self.caller_sigil):
            raise ValidationError({"caller_sigil": _("Caller sigil contains unsupported characters.")})

    @staticmethod
    def _is_safe_caller_sigil(value: str) -> bool:
        """Return whether a caller sigil is safe for downstream recipe expansion."""

        return bool(value) and all(ch.isalnum() or ch in {"_", ".", "-"} for ch in value)

    @staticmethod
    def _is_safe_target_url(value: str) -> bool:
        """Return whether a rendered target URL uses an allowed scheme."""

        parsed = urlparse((value or "").strip())
        if parsed.scheme in {"http", "https"}:
            return bool(parsed.netloc)
        return bool(parsed.path) and not parsed.scheme and not parsed.netloc

    @classmethod
    def from_legacy(
        cls,
        *,
        label: str,
        method: str,
        url: str,
        caller_sigil: str = "",
    ) -> "DashboardAction":
        """Build an unsaved instance from legacy dashboard-action metadata."""

        normalized_method = str(method or cls.HttpMethod.GET).strip().lower()
        if normalized_method not in cls.HttpMethod.values:
            normalized_method = cls.HttpMethod.GET
        return cls(
            slug="legacy-action",
            label=label,
            http_method=normalized_method,
            target_type=cls.TargetType.ABSOLUTE_URL,
            absolute_url=url,
            caller_sigil=caller_sigil,
        )

    def resolve_url(self) -> str:
        """Return the action target URL resolved from the configured target type."""

        if self.target_type == self.TargetType.ADMIN_URL:
            try:
                return reverse(self.admin_url_name)
            except NoReverseMatch:
                if self._is_safe_target_url(self.admin_url_name):
                    return self.admin_url_name
                return ""
        if self.target_type == self.TargetType.ABSOLUTE_URL:
            return self.absolute_url if self._is_safe_target_url(self.absolute_url) else ""
        if self.target_type == self.TargetType.RECIPE:
            if not self.pk:
                return ""
            try:
                return reverse("admin:actions_dashboardaction_execute", args=[self.pk])
            except NoReverseMatch:
                return ""
        return ""

    def as_rendered_action(self) -> dict[str, str | bool]:
        """Return a template-friendly action payload for dashboard row rendering."""

        return {
            "url": self.resolve_url(),
            "label": self.label,
            "method": self.http_method,
            "is_discover": self.label.strip().lower() == "discover",
            "caller_sigil": self.caller_sigil,
        }


class StaffTask(models.Model):
    """Configurable dashboard task shown as a top admin button."""

    slug = models.SlugField(max_length=80, unique=True)
    label = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    admin_url_name = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)
    default_enabled = models.BooleanField(default=True)
    staff_only = models.BooleanField(default=True)
    superuser_only = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "label")
        verbose_name = _("Task Panel")
        verbose_name_plural = _("Task Panels")

    def __str__(self) -> str:
        """Return the display label used in admin controls."""

        return self.label


class StaffTaskPreference(models.Model):
    """Per-user visibility override for a staff task dashboard button."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_task_preferences",
    )
    task = models.ForeignKey(
        StaffTask,
        on_delete=models.CASCADE,
        related_name="user_preferences",
    )
    is_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("task__order", "task__label")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "task"),
                name="actions_stafftaskpreference_unique_user_task",
            )
        ]
        verbose_name = _("Task Panel Preference")
        verbose_name_plural = _("Task Panel Preferences")

    def __str__(self) -> str:
        """Return a readable preference description."""

        return f"{self.user} · {self.task}"


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
        """Return a deterministic HMAC-SHA256 hash for a raw token key."""

        secret = settings.SECRET_KEY.encode("utf-8") if isinstance(settings.SECRET_KEY, str) else settings.SECRET_KEY
        return hmac.new(secret, raw_key.encode("utf-8"), hashlib.sha256).hexdigest()

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

        now = timezone.now()
        if token.last_used_at is None or (now - token.last_used_at) > timezone.timedelta(minutes=1):
            token.last_used_at = now
            token.save(update_fields=["last_used_at"])
        return token
