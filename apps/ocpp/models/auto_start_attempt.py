"""Durable state for automatic remote-start attempts."""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class AutoStartAttempt(models.Model):
    """One idempotent automatic remote-start attempt for an EVSE scope."""

    class State(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        ACCEPTED = "accepted", _("Accepted")
        STARTED = "started", _("Started")
        REJECTED = "rejected", _("Rejected")
        FAILED = "failed", _("Failed")
        TIMED_OUT = "timed_out", _("Timed out")
        RELEASED = "released", _("Released")

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="auto_start_attempts",
    )
    reservation_scope = models.CharField(max_length=64)
    id_tag = models.CharField(max_length=20)
    attempt_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    message_id = models.CharField(max_length=36, unique=True)
    action = models.CharField(max_length=64)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.REQUESTED
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    retry_after = models.DateTimeField(null=True, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Auto-start attempt")
        verbose_name_plural = _("Auto-start attempts")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=("charger", "reservation_scope"),
                condition=Q(state__in=("requested", "accepted", "started")),
                name="unique_active_auto_start_attempt",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger} {self.reservation_scope} {self.state}"
