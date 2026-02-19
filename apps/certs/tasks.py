from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.certs.models import CertificateBase

logger = logging.getLogger(__name__)


def _is_still_due_after_refresh(
    certificate: CertificateBase,
    *,
    now,
) -> bool:
    """Confirm certificate is still due after a fresh expiration read.

    The task already refreshed all certificates once, but renewal can happen later in
    the loop. Performing a second check right before renewal avoids acting on stale
    in-memory state when another process updated certificate files meanwhile.
    """

    expiration = certificate.update_expiration_date()
    return expiration is not None and certificate.is_due_for_renewal(now=now)


@shared_task(name="apps.certs.tasks.refresh_certificate_expirations")
def refresh_certificate_expirations() -> dict[str, int]:
    """Refresh certificate expirations and auto-renew due certificates."""
    now = timezone.now()
    updated = 0
    renewed = 0

    certificates = CertificateBase.objects.select_related(
        "certbotcertificate",
        "selfsignedcertificate",
    )

    for certificate in certificates:
        previous_expiration = certificate.expiration_date
        try:
            expiration = certificate.update_expiration_date()
        except RuntimeError as exc:
            logger.warning(
                "Failed to refresh expiration for certificate %s: %s",
                certificate.pk,
                exc,
            )
            continue
        if expiration != previous_expiration:
            updated += 1

        if certificate.auto_renew and certificate.is_due_for_renewal(now=now):
            try:
                if not _is_still_due_after_refresh(certificate, now=now):
                    continue
                certificate.renew()
            except RuntimeError:
                logger.exception("Failed to auto-renew certificate %s.", certificate.pk)
            else:
                renewed += 1

    if updated or renewed:
        logger.info(
            "Certificate refresh complete: %s updated, %s renewed.",
            updated,
            renewed,
        )

    return {"updated": updated, "renewed": renewed}
