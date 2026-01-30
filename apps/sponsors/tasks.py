"""Celery tasks for sponsor renewals."""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import Sponsorship, SponsorshipPayment

logger = logging.getLogger(__name__)


@shared_task
def process_sponsorship_renewals() -> dict[str, int]:
    """Process sponsorships that are due for renewal."""

    now = timezone.now()
    due = Sponsorship.objects.filter(
        status=Sponsorship.Status.ACTIVE,
        renewal_mode__in=(
            Sponsorship.RenewalMode.MONTHLY,
            Sponsorship.RenewalMode.YEARLY,
        ),
        next_renewal_at__lte=now,
    ).select_related("tier", "user")

    processed = 0
    skipped = 0

    for sponsorship in due:
        try:
            with transaction.atomic():
                processor = sponsorship.last_payment_processor()
                SponsorshipPayment.objects.create(
                    sponsorship=sponsorship,
                    amount=sponsorship.tier.amount,
                    currency=sponsorship.tier.currency,
                    status=SponsorshipPayment.Status.PENDING,
                    kind=SponsorshipPayment.Kind.RENEWAL,
                    processor=processor,
                )
                sponsorship.status = Sponsorship.Status.PAST_DUE
                sponsorship.save(update_fields=["status"])
            processed += 1
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to renew sponsorship %s", sponsorship.pk)
            skipped += 1

    return {"processed": processed, "skipped": skipped}
