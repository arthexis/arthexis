from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.core.notifications import CUSTOM_CHANNEL_PATTERN, LcdChannel


class RFIDWatchlistEntry(Entity):
    """Operator-managed RFID watchlist entry with allowlisted side effects."""

    ALLOWED_ACTION_CONFIG_KEYS = {
        "body",
        "lcd_channel_num",
        "lcd_channel_type",
        "reach",
        "subject",
    }
    ALLOWED_LCD_CHANNEL_TYPES = {channel.value for channel in LcdChannel} | {
        "all",
        "full",
    }
    NET_MESSAGE_SUPPRESSED_LCD_CHANNEL_TYPES = {"none", "off", "disabled"}

    class ActionType(models.TextChoices):
        AUDIT = "audit", _("Audit only")
        LOCAL_NOTIFICATION = "local_notification", _("Local notification")
        NET_MESSAGE = "net_message", _("Net message")

    label = models.ForeignKey(
        "cards.RFID",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="watchlist_entries",
        help_text="Optional RFID row to match. The normalized RFID value is also supported.",
    )
    normalized_rfid = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text="Normalized RFID value to match when no RFID row is selected.",
    )
    name = models.CharField(max_length=80, blank=True)
    enabled = models.BooleanField(default=True, db_index=True)
    action_type = models.CharField(
        max_length=32,
        choices=ActionType.choices,
        default=ActionType.AUDIT,
    )
    action_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Allowlisted action options such as subject, body, reach, or LCD channel.",
    )
    rate_limit_seconds = models.PositiveIntegerField(
        default=60,
        help_text="Minimum seconds between delivered action attempts for this entry.",
    )
    max_retries = models.PositiveSmallIntegerField(default=3)
    last_matched_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "normalized_rfid", "pk"]
        verbose_name = _("RFID Watchlist Entry")
        verbose_name_plural = _("RFID Watchlist Entries")
        indexes = [
            models.Index(fields=["enabled", "normalized_rfid"]),
            models.Index(fields=["enabled", "label"]),
        ]

    def clean(self) -> None:
        super().clean()
        errors = {}
        if not self.label_id and not self.normalized_rfid:
            errors["normalized_rfid"] = _(
                "Select an RFID row or provide a normalized RFID value."
            )

        config = self.action_config
        if not isinstance(config, dict):
            errors["action_config"] = _("Action config must be a JSON object.")
        else:
            config_errors = []
            unsupported_keys = sorted(set(config) - self.ALLOWED_ACTION_CONFIG_KEYS)
            if unsupported_keys:
                config_errors.append(
                    _("Unsupported action config keys: %(keys)s")
                    % {"keys": ", ".join(unsupported_keys)}
                )
            channel_type = config.get("lcd_channel_type")
            if channel_type not in (None, ""):
                normalized_channel_type = str(channel_type).strip().lower()
                is_known_channel = (
                    normalized_channel_type in self.ALLOWED_LCD_CHANNEL_TYPES
                )
                is_net_message_suppression = (
                    self.action_type == self.ActionType.NET_MESSAGE
                    and normalized_channel_type
                    in self.NET_MESSAGE_SUPPRESSED_LCD_CHANNEL_TYPES
                )
                is_safe_custom_channel = bool(
                    CUSTOM_CHANNEL_PATTERN.fullmatch(normalized_channel_type)
                    and normalized_channel_type
                    not in self.NET_MESSAGE_SUPPRESSED_LCD_CHANNEL_TYPES
                )
                if not (
                    is_known_channel
                    or is_net_message_suppression
                    or is_safe_custom_channel
                ):
                    config_errors.append(
                        _(
                            "LCD channel type must be one of: %(values)s, "
                            "a safe custom channel name, or a NetMessage "
                            "suppression value."
                        )
                        % {"values": ", ".join(sorted(self.ALLOWED_LCD_CHANNEL_TYPES))}
                    )
            if config_errors:
                errors["action_config"] = config_errors

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.normalized_rfid:
            from apps.cards.models import RFID

            self.normalized_rfid = RFID.normalize_code(self.normalized_rfid)
        super().save(*args, **kwargs)

    def is_rate_limited(self, now=None) -> bool:
        if not self.last_matched_at or not self.rate_limit_seconds:
            return False
        current = now or timezone.now()
        return current - self.last_matched_at < timedelta(
            seconds=self.rate_limit_seconds
        )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name or self.normalized_rfid or str(self.label_id or "")


class RFIDWatchlistEvent(Entity):
    """Durable outbox row for RFID watchlist actions."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        DELIVERED = "delivered", _("Delivered")
        FAILED = "failed", _("Failed")
        RATE_LIMITED = "rate_limited", _("Rate limited")
        SUPPRESSED = "suppressed", _("Suppressed")

    entry = models.ForeignKey(
        RFIDWatchlistEntry,
        on_delete=models.CASCADE,
        related_name="events",
    )
    attempt = models.ForeignKey(
        "cards.RFIDAttempt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="watchlist_events",
    )
    label = models.ForeignKey(
        "cards.RFID",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="watchlist_events",
    )
    rfid = models.CharField(max_length=255, db_index=True)
    source = models.CharField(max_length=32, blank=True, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    idempotency_key = models.CharField(max_length=120, unique=True)
    match_payload = models.JSONField(default=dict, blank=True)
    action_output = models.TextField(blank=True)
    action_error = models.TextField(blank=True)
    queue_error = models.TextField(blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = _("RFID Watchlist Event")
        verbose_name_plural = _("RFID Watchlist Events")
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["source", "created_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.rfid} {self.status}"
