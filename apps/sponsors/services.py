"""Service helpers for sponsor registrations."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import SponsorTier, Sponsorship, SponsorshipPayment


@dataclass(frozen=True)
class SponsorRegistrationResult:
    user: object
    sponsorship: Sponsorship
    payment: SponsorshipPayment


def register_sponsor(
    *,
    username: str,
    email: str,
    password: str,
    tier: SponsorTier,
    renewal_mode: str,
    payment_processor,
    payment_reference: str = "",
) -> SponsorRegistrationResult:
    """Create a new sponsor account, sponsorship, and payment entry."""

    if tier is None or not tier.is_active:
        raise ValidationError({"tier": _("Selected sponsor tier is unavailable.")})
    if payment_processor is None:
        raise ValidationError(
            {"payment_processor": _("Select a configured payment processor.")}
        )

    now = timezone.now()
    user_model = get_user_model()

    with transaction.atomic():
        user = user_model.objects.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=False,
            is_superuser=False,
        )
        sponsorship = Sponsorship.objects.create(
            user=user,
            tier=tier,
            renewal_mode=renewal_mode,
            started_at=now,
            status=Sponsorship.Status.PAST_DUE,
        )
        payment = SponsorshipPayment.objects.create(
            sponsorship=sponsorship,
            amount=tier.amount,
            currency=tier.currency,
            status=SponsorshipPayment.Status.PENDING,
            kind=SponsorshipPayment.Kind.INITIAL,
            processor=payment_processor,
            external_reference=payment_reference or "",
        )

    return SponsorRegistrationResult(
        user=user, sponsorship=sponsorship, payment=payment
    )
