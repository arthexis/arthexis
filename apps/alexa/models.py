from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models.ownable import Ownable


class AlexaCredentialsError(ValueError):
    """Raised when Alexa account credentials are incomplete or invalid."""


class AlexaAccount(Ownable):
    """Ownable Alexa account with OAuth credentials used to send reminders."""

    name = models.CharField(max_length=150)
    client_id = models.CharField(max_length=255)
    client_secret = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    api_base_url = models.URLField(
        default="https://api.amazonalexa.com",
        help_text=_("Base Alexa API URL used for reminder operations."),
    )
    is_active = models.BooleanField(default=True)
    last_credentials_check_at = models.DateTimeField(null=True, blank=True)
    last_credentials_check_message = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("name",)
        verbose_name = _("Alexa account")
        verbose_name_plural = _("Alexa accounts")
        constraints = [
            models.UniqueConstraint(
                fields=("name", "user", "group"),
                name="alexa_account_unique_owner_name",
            )
        ]

    def __str__(self) -> str:
        """Return the configured account name."""
        return self.name

    def clean(self) -> None:
        """Validate ownership and required credentials."""
        super().clean()
        missing_fields = [
            field
            for field in ("client_id", "client_secret", "refresh_token")
            if not getattr(self, field, "").strip()
        ]
        if missing_fields:
            raise ValidationError(
                {
                    field: _("This credential value is required.")
                    for field in missing_fields
                }
            )

    def validate_credentials(self) -> None:
        """Perform lightweight checks for required credential values."""
        if not self.client_id.strip():
            raise AlexaCredentialsError("Missing Alexa client ID.")
        if not self.client_secret.strip():
            raise AlexaCredentialsError("Missing Alexa client secret.")
        if not self.refresh_token.strip():
            raise AlexaCredentialsError("Missing Alexa refresh token.")


class AlexaReminder(Ownable):
    """Ownable reminder template that can be sent to one or more Alexa accounts."""

    name = models.CharField(max_length=150)
    event_key = models.CharField(
        max_length=120,
        help_text=_("Stable event key used when external events update this reminder."),
    )
    content = models.TextField(help_text=_("Reminder text sent to Alexa accounts."))
    scheduled_for = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Optional explicit reminder datetime."),
    )
    auto_update_on_event = models.BooleanField(
        default=True,
        help_text=_("Automatically mark deliveries for update when reminder events change."),
    )
    target_accounts = models.ManyToManyField(
        AlexaAccount,
        related_name="targeted_reminders",
        through="AlexaReminderDelivery",
        blank=True,
        help_text=_("Alexa accounts that should receive this reminder."),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = _("Alexa reminder")
        verbose_name_plural = _("Alexa reminders")
        constraints = [
            models.UniqueConstraint(
                fields=("event_key", "user", "group"),
                name="alexa_reminder_unique_owner_event",
            )
        ]

    def __str__(self) -> str:
        """Return human-readable reminder name."""
        return self.name

    def mark_event_update(self) -> int:
        """Flag existing deliveries for update after reminder event changes."""
        if not self.pk or not self.auto_update_on_event:
            return 0
        return self.deliveries.exclude(status=AlexaReminderDelivery.STATUS_PENDING).update(
            status=AlexaReminderDelivery.STATUS_UPDATE_PENDING
        )


class AlexaReminderDelivery(models.Model):
    """Per-account delivery state for an Alexa reminder."""

    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_UPDATE_PENDING = "update_pending"
    STATUS_UPDATED = "updated"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = (
        (STATUS_PENDING, _("Pending send")),
        (STATUS_SENT, _("Sent")),
        (STATUS_UPDATE_PENDING, _("Pending update")),
        (STATUS_UPDATED, _("Updated")),
        (STATUS_FAILED, _("Failed")),
    )

    reminder = models.ForeignKey(
        AlexaReminder,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    account = models.ForeignKey(
        AlexaAccount,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    remote_reference = models.CharField(max_length=255, blank=True)
    event_payload = models.JSONField(default=dict, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("reminder__name", "account__name")
        verbose_name = _("Alexa reminder delivery")
        verbose_name_plural = _("Alexa reminder deliveries")
        constraints = [
            models.UniqueConstraint(
                fields=("reminder", "account"),
                name="alexa_reminder_delivery_unique_pair",
            )
        ]

    def __str__(self) -> str:
        """Return compact delivery status label."""
        return f"{self.reminder} -> {self.account} ({self.status})"
