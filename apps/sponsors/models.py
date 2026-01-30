"""Sponsorship tiers and memberships."""

from __future__ import annotations

import calendar
from datetime import datetime
from typing import Iterable

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.groups.models import SecurityGroup
from apps.payments.models import OpenPayProcessor, PayPalProcessor, StripeProcessor


PAYMENT_PROCESSOR_MODELS: tuple[type[models.Model], ...] = (
    OpenPayProcessor,
    PayPalProcessor,
    StripeProcessor,
)


def add_months(value: datetime, months: int) -> datetime:
    """Return ``value`` advanced by the given number of months."""

    if months == 0:
        return value
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


class SponsorTier(Entity):
    """Sponsor tiers that map to price points and security groups."""

    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8, default="USD")
    is_active = models.BooleanField(default=True)
    security_groups = models.ManyToManyField(
        SecurityGroup,
        related_name="sponsor_tiers",
        blank=True,
    )

    class Meta:
        verbose_name = _("Sponsor tier")
        verbose_name_plural = _("Sponsor tiers")

    def __str__(self) -> str:  # pragma: no cover - presentation only
        return f"{self.name} ({self.amount} {self.currency})"


class Sponsorship(Entity):
    """A sponsor membership for a user."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        PAST_DUE = "past_due", _("Past due")
        CANCELED = "canceled", _("Canceled")
        EXPIRED = "expired", _("Expired")

    class RenewalMode(models.TextChoices):
        MONTHLY = "monthly", _("Monthly")
        YEARLY = "yearly", _("Yearly")
        MANUAL = "manual", _("Manual")

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="sponsorships",
    )
    tier = models.ForeignKey(
        SponsorTier,
        on_delete=models.PROTECT,
        related_name="sponsorships",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    renewal_mode = models.CharField(
        max_length=20, choices=RenewalMode.choices, default=RenewalMode.MONTHLY
    )
    started_at = models.DateTimeField(default=timezone.now)
    last_renewed_at = models.DateTimeField(null=True, blank=True)
    next_renewal_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Sponsorship")
        verbose_name_plural = _("Sponsorships")

    def clean(self):
        super().clean()
        if getattr(self.user, "is_staff", False):
            raise ValidationError({"user": _("Staff accounts cannot be sponsors.")})

    def save(self, *args, **kwargs):
        if self.next_renewal_at is None:
            self.next_renewal_at = next_renewal_date(self.started_at, self.renewal_mode)
        super().save(*args, **kwargs)

    def apply_tier_groups(self) -> None:
        """Ensure the user is in the tier's configured security groups."""

        groups = list(self.tier.security_groups.all())
        if groups:
            self.user.groups.add(*groups)

    def mark_renewed(self, renewed_at: datetime | None = None) -> None:
        """Update renewal dates for this sponsorship."""

        renewed_at = renewed_at or timezone.now()
        self.last_renewed_at = renewed_at
        self.next_renewal_at = next_renewal_date(renewed_at, self.renewal_mode)
        self.save(update_fields=["last_renewed_at", "next_renewal_at"])

    def last_payment_processor(self):
        payment = self.payments.order_by("-processed_at", "-pk").first()
        if payment:
            return payment.processor
        return None


def next_renewal_date(started_at: datetime, mode: str) -> datetime | None:
    """Return the next renewal date for the given mode."""

    if mode == Sponsorship.RenewalMode.MANUAL:
        return None
    if mode == Sponsorship.RenewalMode.YEARLY:
        return add_months(started_at, 12)
    return add_months(started_at, 1)


class SponsorshipPayment(Entity):
    """Record sponsorship payments through configured processors."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        REFUNDED = "refunded", _("Refunded")

    class Kind(models.TextChoices):
        INITIAL = "initial", _("Initial")
        RENEWAL = "renewal", _("Renewal")

    sponsorship = models.ForeignKey(
        Sponsorship,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8, default="USD")
    status = models.CharField(max_length=20, choices=Status.choices)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.INITIAL)

    processor_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    processor_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    processor = GenericForeignKey("processor_content_type", "processor_object_id")

    external_reference = models.CharField(max_length=255, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Sponsorship payment")
        verbose_name_plural = _("Sponsorship payments")

    def clean(self):
        super().clean()
        if self.processor_content_type_id and self.processor_object_id:
            model = self.processor_content_type.model_class()
            if model not in PAYMENT_PROCESSOR_MODELS:
                raise ValidationError(
                    {
                        "processor_content_type": _(
                            "Unsupported payment processor for sponsorships."
                        )
                    }
                )

    def __str__(self) -> str:  # pragma: no cover - presentation only
        return f"{self.sponsorship_id} - {self.amount} {self.currency}"


def configured_payment_processors() -> Iterable[models.Model]:
    """Return configured payment processors available for sponsorships."""

    processors: list[models.Model] = []
    for model in PAYMENT_PROCESSOR_MODELS:
        for processor in model.objects.all():
            if hasattr(processor, "has_credentials") and not processor.has_credentials():
                continue
            processors.append(processor)
    return processors
