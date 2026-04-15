from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.base.models import Entity
from apps.cards.soul import PACKAGE_MAX_BYTES


def default_session_expiry():
    return timezone.now() + timedelta(hours=24)


class SoulRegistrationSession(Entity):
    class State(models.TextChoices):
        STARTED = "started", "Started"
        OFFERING_DONE = "offering_done", "Offering Completed"
        SURVEY_DONE = "survey_done", "Survey Completed"
        EMAIL_SENT = "email_sent", "Email Sent"
        VERIFIED = "verified", "Verified"
        COMPLETED = "completed", "Completed"

    email = models.EmailField()
    offering_soul = models.ForeignKey(
        "cards.OfferingSoul",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="registration_sessions",
    )
    survey_response = models.ForeignKey(
        "survey.SurveyResponse",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="registration_sessions",
    )
    participant_token = models.CharField(max_length=64, blank=True, default="")
    state = models.CharField(max_length=32, choices=State.choices, default=State.STARTED)
    verification_token_hash = models.CharField(max_length=64, blank=True, default="")
    verification_sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(default=default_session_expiry)
    ip_hash = models.CharField(max_length=64, blank=True, default="")
    ua_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ("-id",)

    @staticmethod
    def digest_value(value: str) -> str:
        return hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def create_verification_token(cls) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        return token, cls.digest_value(token)

    def verify_token(self, token: str) -> bool:
        if not self.verification_token_hash:
            return False
        return secrets.compare_digest(self.verification_token_hash, self.digest_value(token))


class Soul(Entity):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="soul",
    )
    offering_soul = models.ForeignKey(
        "cards.OfferingSoul",
        on_delete=models.PROTECT,
        related_name="souls",
    )
    survey_response = models.ForeignKey(
        "survey.SurveyResponse",
        on_delete=models.PROTECT,
        related_name="souls",
    )
    soul_id = models.CharField(max_length=64, unique=True)
    survey_digest = models.CharField(max_length=64)
    package_version = models.CharField(max_length=16, default="1.0")
    sig_alg = models.CharField(max_length=32, default="none")
    kid = models.CharField(max_length=64, blank=True, default="")
    signature = models.TextField(blank=True, default="")
    package = models.JSONField(default=dict, blank=True)
    package_bytes = models.BinaryField(blank=True, null=True)
    email_hash = models.CharField(max_length=64, blank=True, default="")
    email_verified_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-id",)

    def clean(self):
        super().clean()
        encoded = json.dumps(self.package or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > PACKAGE_MAX_BYTES:
            raise ValidationError({"package": "Soul Seed package exceeds 512 KB limit."})
        if self.package_bytes and len(self.package_bytes) > PACKAGE_MAX_BYTES:
            raise ValidationError({"package_bytes": "Soul Seed package bytes exceed 512 KB limit."})


class ShopOrderSoulAttachment(Entity):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        WRITTEN = "written", "Written"
        FAILED = "failed", "Failed"

    order_item = models.OneToOneField(
        "shop.ShopOrderItem",
        on_delete=models.CASCADE,
        related_name="soul_attachment",
    )
    soul = models.ForeignKey(
        "souls.Soul",
        on_delete=models.PROTECT,
        related_name="order_attachments",
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ("-id",)
