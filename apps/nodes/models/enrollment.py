from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.sites.models import Site
from django.db import models
from django.utils import timezone


class NodeEnrollment(models.Model):
    class Status(models.TextChoices):
        ISSUED = "ISSUED", "Issued"
        PUBLIC_KEY_SUBMITTED = "PUBLIC_KEY_SUBMITTED", "Public key submitted"
        ACTIVE = "ACTIVE", "Active"
        REVOKED = "REVOKED", "Revoked"
        EXPIRED = "EXPIRED", "Expired"

    node = models.ForeignKey("nodes.Node", on_delete=models.CASCADE, related_name="enrollments")
    site = models.ForeignKey(Site, on_delete=models.SET_NULL, null=True, blank=True, related_name="node_enrollments")
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_node_enrollments",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    token_hint = models.CharField(max_length=8)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ISSUED)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @classmethod
    def issue(cls, *, node, site=None, issued_by=None, ttl: timedelta = timedelta(hours=1)):
        token = secrets.token_urlsafe(24)
        enrollment = cls.objects.create(
            node=node,
            site=site,
            issued_by=issued_by,
            token_hash=cls.hash_token(token),
            token_hint=token[-6:],
            expires_at=timezone.now() + ttl,
        )
        return enrollment, token

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at


class NodeEnrollmentEvent(models.Model):
    class Action(models.TextChoices):
        TOKEN_ISSUED = "TOKEN_ISSUED", "Token issued"
        TOKEN_REISSUED = "TOKEN_REISSUED", "Token reissued"
        PUBLIC_KEY_SUBMITTED = "PUBLIC_KEY_SUBMITTED", "Public key submitted"
        APPROVED = "APPROVED", "Approved"
        REVOKED = "REVOKED", "Revoked"
        KEY_ROTATED = "KEY_ROTATED", "Key rotated"

    node = models.ForeignKey("nodes.Node", on_delete=models.CASCADE, related_name="enrollment_events")
    enrollment = models.ForeignKey(
        "nodes.NodeEnrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    from_state = models.CharField(max_length=32, blank=True)
    to_state = models.CharField(max_length=32, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="node_enrollment_events",
    )
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
