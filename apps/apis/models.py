"""Models for API explorer entries and self-service service tokens."""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class APIExplorerManager(models.Manager):
    """Manager that supports natural key lookups for fixture loading."""

    def get_by_natural_key(self, name: str):  # pragma: no cover - fixture hook
        """Resolve an API explorer by its unique ``name`` natural key."""

        return self.get(name=name)


class APIExplorer(models.Model):
    """Represents a configurable API entry point."""

    name = models.CharField(max_length=120, unique=True)
    base_url = models.URLField(
        max_length=500,
        help_text="Base URL for this API, such as https://api.example.com/v1.",
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = APIExplorerManager()

    class Meta:
        ordering = ("name",)
        verbose_name = "API Explorer"
        verbose_name_plural = "API Explorers"

    def __str__(self) -> str:
        """Return a readable label for this API entry point."""

        return self.name

    def natural_key(self) -> tuple[str]:  # pragma: no cover - fixture hook
        """Expose ``name`` as this model's natural key for fixtures."""

        return (self.name,)


class ResourceMethod(models.Model):
    """Defines a resource+method operation for an API explorer entry point."""

    class HttpMethod(models.TextChoices):
        """Supported HTTP methods for resource method operations."""

        GET = "GET", "GET"
        POST = "POST", "POST"
        PUT = "PUT", "PUT"
        PATCH = "PATCH", "PATCH"
        DELETE = "DELETE", "DELETE"

    api = models.ForeignKey(APIExplorer, on_delete=models.CASCADE, related_name="resource_methods")
    operation_name = models.CharField(max_length=150)
    resource_path = models.CharField(max_length=255, help_text="Relative path, e.g. /users/{id}.")
    http_method = models.CharField(max_length=10, choices=HttpMethod.choices)
    request_structure = models.JSONField(default=dict, blank=True)
    response_structure = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("api__name", "resource_path", "http_method", "operation_name")
        verbose_name = "Resource Method"
        verbose_name_plural = "Resource Methods"
        constraints = [
            models.UniqueConstraint(
                fields=("api", "resource_path", "http_method", "operation_name"),
                name="apis_resource_method_unique_operation",
            )
        ]

    def __str__(self) -> str:
        """Return a readable resource method label."""

        return f"{self.api.name}: {self.http_method} {self.resource_path} ({self.operation_name})"

    def clean(self) -> None:
        """Validate resource path and request/response structures."""

        super().clean()
        if not self.resource_path.startswith("/"):
            raise ValidationError({"resource_path": "Resource path must start with '/'."})

        for field_name in ("request_structure", "response_structure"):
            payload = getattr(self, field_name)
            if payload in (None, ""):
                setattr(self, field_name, dict())
            elif not isinstance(payload, (dict, list)):
                raise ValidationError({field_name: "Structure must be a JSON object or array."})


class ServiceToken(models.Model):
    """Scoped service token with lifecycle metadata and expiry policy enforcement."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REPLACED = "replaced", "Replaced"
        REVOKED = "revoked", "Revoked"

    MAX_EXPIRY_DAYS = 90

    name = models.CharField(max_length=120)
    token_prefix = models.CharField(max_length=24, db_index=True)
    secret_hash = models.CharField(max_length=255)
    scopes = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="issued_service_tokens",
    )
    rotated_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rotated_to",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Service Token"
        verbose_name_plural = "Service Tokens"
        permissions = [
            ("manage_service_tokens", "Can manage service token lifecycle"),
            ("reveal_service_token_secret", "Can reveal newly created service token secrets"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.token_prefix})"

    @classmethod
    def issue(
        cls,
        *,
        actor,
        name: str,
        scopes: list[str],
        expires_at,
        rotated_from=None,
    ) -> tuple["ServiceToken", str]:
        raw_secret = f"atk_{secrets.token_urlsafe(30)}"
        secret_hash = make_password(raw_secret)
        token_prefix = raw_secret[:16]
        token = cls.objects.create(
            name=name.strip(),
            token_prefix=token_prefix,
            secret_hash=secret_hash,
            scopes=sorted({scope.strip() for scope in scopes if scope and scope.strip()}),
            expires_at=expires_at,
            created_by=actor,
            rotated_from=rotated_from,
        )
        ServiceTokenEvent.record(
            token=token,
            event_type=ServiceTokenEvent.EventType.CREATED,
            actor=actor,
            details={
                "expires_at": expires_at.isoformat(),
                "scope_count": len(token.scopes),
                "rotated_from_id": rotated_from.pk if rotated_from else None,
            },
        )
        return token, raw_secret

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def clean(self) -> None:
        super().clean()
        errors = {}
        if not isinstance(self.scopes, list) or any(not isinstance(item, str) for item in self.scopes):
            errors["scopes"] = "Scopes must be a list of strings."
        if self.expires_at:
            now = timezone.now()
            maximum_expiry = now + timedelta(days=self.MAX_EXPIRY_DAYS)
            if self.expires_at <= now:
                errors["expires_at"] = "Expiry must be in the future."
            elif self.expires_at > maximum_expiry:
                errors["expires_at"] = (
                    f"Expiry exceeds policy limit of {self.MAX_EXPIRY_DAYS} days."
                )
        if errors:
            raise ValidationError(errors)

    def check_secret(self, raw_secret: str) -> bool:
        return check_password(raw_secret, self.secret_hash)

    def revoke(self, *, actor, reason: str, impact_note: str = "") -> None:
        self.status = self.Status.REVOKED
        self.revoked_at = timezone.now()
        self.revoked_reason = reason.strip()
        self.save(update_fields=["status", "revoked_at", "revoked_reason", "updated_at"])
        ServiceTokenEvent.record(
            token=self,
            event_type=ServiceTokenEvent.EventType.REVOKED,
            actor=actor,
            details={"reason": reason, "impact_note": impact_note},
        )


class ServiceTokenEvent(models.Model):
    """Audit stream for service token lifecycle and reveal actions."""

    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        REVEALED = "revealed", "Secret Revealed"
        REVOKED = "revoked", "Revoked"
        ROTATED = "rotated", "Rotated"

    token = models.ForeignKey(ServiceToken, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="service_token_events",
    )
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Service Token Event"
        verbose_name_plural = "Service Token Events"

    def __str__(self) -> str:
        actor = getattr(self.actor, "username", "system")
        return f"{self.token_id}:{self.event_type}:{actor}"

    @classmethod
    def record(cls, *, token: ServiceToken, event_type: str, actor, details: dict | None = None):
        payload = details or {}
        payload.setdefault("audit_fingerprint", hashlib.sha256(
            f"{token.pk}:{event_type}:{timezone.now().isoformat()}".encode()
        ).hexdigest()[:16])
        return cls.objects.create(token=token, event_type=event_type, actor=actor, details=payload)
