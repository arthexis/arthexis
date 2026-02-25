from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField

from apps.core.models.ownable import Ownable


class AlexaCredentialsError(ValueError):
    """Raised when Alexa account credentials are incomplete or invalid."""


class AlexaAccount(Ownable):
    """Ownable Alexa account with OAuth credentials used to send reminders."""

    name = models.CharField(max_length=150)
    client_id = models.CharField(max_length=255)
    client_secret = EncryptedCharField(max_length=255)
    refresh_token = EncryptedCharField(max_length=255)
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
                fields=("name", "user"),
                condition=Q(user__isnull=False, group__isnull=True),
                name="alexa_account_unique_user_name",
            ),
            models.UniqueConstraint(
                fields=("name", "group"),
                condition=Q(user__isnull=True, group__isnull=False),
                name="alexa_account_unique_group_name",
            ),
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
            if not (getattr(self, field, "") or "").strip()
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
        if not (self.client_id or "").strip():
            raise AlexaCredentialsError("Missing Alexa client ID.")
        if not (self.client_secret or "").strip():
            raise AlexaCredentialsError("Missing Alexa client secret.")
        if not (self.refresh_token or "").strip():
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
                fields=("event_key", "user"),
                condition=Q(user__isnull=False, group__isnull=True),
                name="alexa_reminder_unique_user_event",
            ),
            models.UniqueConstraint(
                fields=("event_key", "group"),
                condition=Q(user__isnull=True, group__isnull=False),
                name="alexa_reminder_unique_group_event",
            ),
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

    def clean(self) -> None:
        """Ensure reminder and account ownership match."""
        super().clean()
        if not self.reminder_id or not self.account_id:
            return
        if (
            self.reminder.user_id != self.account.user_id
            or self.reminder.group_id != self.account.group_id
        ):
            raise ValidationError(_("Reminder and account must belong to the same owner."))

    def save(self, *args, **kwargs):
        """Validate ownership invariants before persisting."""
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return compact delivery status label."""
        return f"{self.reminder} -> {self.account} ({self.status})"
