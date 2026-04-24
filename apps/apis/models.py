"""Models for API explorer entries and self-service service tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.groups.models import SecurityGroup


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


class GeneralServiceToken(models.Model):
    """Manual JWT bearer token bound to a user and optional security group filter."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"
        REVOKED = "revoked", "Revoked"

    MAX_EXPIRY_DAYS = 90

    name = models.CharField(max_length=120)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="general_service_tokens",
    )
    token_prefix = models.CharField(max_length=40, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    security_groups = models.ManyToManyField(
        SecurityGroup,
        blank=True,
        related_name="general_service_tokens",
        help_text="Optional SG filter. Empty means all SGs the user can access.",
    )
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    retired_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=300, blank=True, default="")
    claims = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="issued_general_service_tokens",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "General Service Token"
        verbose_name_plural = "General Service Tokens"
        permissions = [
            ("manage_general_service_tokens", "Can manage general service token lifecycle"),
            ("reveal_general_service_token_secret", "Can reveal newly created general service token secrets"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.token_prefix})"

    @staticmethod
    def _urlsafe_b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    @staticmethod
    def _urlsafe_decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    @classmethod
    def _encode_jwt(cls, payload: dict) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        header_part = cls._urlsafe_b64(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        payload_part = cls._urlsafe_b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signing_input = f"{header_part}.{payload_part}".encode("utf-8")
        signature = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        signature_part = cls._urlsafe_b64(signature)
        return f"{header_part}.{payload_part}.{signature_part}"

    @classmethod
    def _decode_jwt(cls, token: str) -> dict | None:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        signing_input = f"{parts[0]}.{parts[1]}".encode("utf-8")
        expected_signature = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        provided_signature = cls._urlsafe_decode(parts[2])
        if not hmac.compare_digest(expected_signature, provided_signature):
            return None
        payload_bytes = cls._urlsafe_decode(parts[1])
        payload = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        return payload

    @classmethod
    def issue(
        cls,
        *,
        actor,
        user,
        name: str,
        expires_at,
        security_groups: list[SecurityGroup] | None = None,
        claims: dict | None = None,
    ) -> tuple["GeneralServiceToken", str]:
        issued_at = timezone.now()
        selected_groups = list(security_groups or [])
        group_ids = sorted({group.id for group in selected_groups})
        token_claims = {
            "sub": str(user.pk),
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": secrets.token_urlsafe(16),
            "token_type": "general_service",
            "sg_ids": group_ids,
            **(claims or {}),
        }
        raw_token = cls._encode_jwt(token_claims)
        token = cls.objects.create(
            name=name.strip(),
            user=user,
            token_prefix=raw_token[:40],
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            expires_at=expires_at,
            claims=claims or {},
            created_by=actor,
        )
        if selected_groups:
            token.security_groups.set(selected_groups)
        GeneralServiceTokenEvent.record(
            token=token,
            event_type=GeneralServiceTokenEvent.EventType.CREATED,
            actor=actor,
            details={"expires_at": expires_at.isoformat(), "sg_count": len(group_ids)},
        )
        return token, raw_token

    @classmethod
    def retire_expired_tokens(cls) -> int:
        now = timezone.now()
        updated = cls.objects.filter(
            status=cls.Status.ACTIVE,
            expires_at__lte=now,
        ).update(
            status=cls.Status.RETIRED,
            retired_at=now,
            updated_at=now,
        )
        return int(updated)

    @classmethod
    def authenticate_jwt(cls, raw_token: str) -> tuple["GeneralServiceToken" | None, dict | None, str]:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        token = cls.objects.select_related("user").filter(token_hash=token_hash).first()
        if token is None:
            return None, None, "token_invalid"
        if token.status == cls.Status.REVOKED:
            return None, None, "token_revoked"
        if token.status == cls.Status.RETIRED:
            return None, None, "token_retired"
        if token.expires_at <= timezone.now():
            token.status = cls.Status.RETIRED
            token.retired_at = timezone.now()
            token.save(update_fields=["status", "retired_at", "updated_at"])
            GeneralServiceTokenEvent.record(
                token=token,
                event_type=GeneralServiceTokenEvent.EventType.RETIRED,
                actor=None,
                details={"reason": "expired"},
            )
            return None, None, "token_expired"
        payload = cls._decode_jwt(raw_token)
        if payload is None:
            return None, None, "token_signature_invalid"
        return token, payload, ""

    def clean(self) -> None:
        super().clean()
        errors = {}
        now = timezone.now()
        if self.expires_at <= now:
            errors["expires_at"] = "Expiry must be in the future."
        elif self.expires_at > now + timedelta(days=self.MAX_EXPIRY_DAYS):
            errors["expires_at"] = f"Expiry exceeds policy limit of {self.MAX_EXPIRY_DAYS} days."
        if errors:
            raise ValidationError(errors)

    def allowed_security_group_ids(self) -> set[int]:
        explicit_group_ids = set(self.security_groups.values_list("id", flat=True))
        if explicit_group_ids:
            return explicit_group_ids
        return set(self.user.groups.values_list("id", flat=True))

    def can_access_security_group(self, group_id: int) -> bool:
        user_group_ids = set(self.user.groups.values_list("id", flat=True))
        allowed = self.allowed_security_group_ids()
        return group_id in user_group_ids and group_id in allowed

    def revoke(self, *, actor, reason: str, impact_note: str = "") -> None:
        self.status = self.Status.REVOKED
        self.revoked_at = timezone.now()
        self.revoked_reason = reason.strip()
        self.save(update_fields=["status", "revoked_at", "revoked_reason", "updated_at"])
        GeneralServiceTokenEvent.record(
            token=self,
            event_type=GeneralServiceTokenEvent.EventType.REVOKED,
            actor=actor,
            details={"reason": reason, "impact_note": impact_note},
        )


class GeneralServiceTokenEvent(models.Model):
    """Audit stream for general service token lifecycle and reveal actions."""

    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        RETIRED = "retired", "Retired"
        REVEALED = "revealed", "Secret Revealed"
        REVOKED = "revoked", "Revoked"

    token = models.ForeignKey(
        GeneralServiceToken,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="general_service_token_events",
    )
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "General Service Token Event"
        verbose_name_plural = "General Service Token Events"

    def __str__(self) -> str:
        actor = getattr(self.actor, "username", "system")
        return f"{self.token_id}:{self.event_type}:{actor}"

    @classmethod
    def record(cls, *, token: GeneralServiceToken, event_type: str, actor, details: dict | None = None):
        payload = details or {}
        payload.setdefault("audit_fingerprint", hashlib.sha256(
            f"{token.pk}:{event_type}:{timezone.now().isoformat()}".encode()
        ).hexdigest()[:16])
        return cls.objects.create(token=token, event_type=event_type, actor=actor, details=payload)
