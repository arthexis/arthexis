from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.certs.models import CertificateBase

logger = logging.getLogger(__name__)


@shared_task(name="apps.certs.tasks.refresh_certificate_expirations")
def refresh_certificate_expirations() -> dict[str, int]:
    now = timezone.now()
    updated = 0
    renewed = 0

    certificates = CertificateBase.objects.select_related(
        "certbotcertificate",
        "selfsignedcertificate",
    )

    for certificate in certificates:
        if certificate.expiration_date is None:
            try:
                expiration = certificate.update_expiration_date()
            except RuntimeError as exc:
                logger.warning(
                    "Failed to refresh expiration for certificate %s: %s",
                    certificate.pk,
                    exc,
                )
                continue
            if expiration is not None:
                updated += 1

        if certificate.auto_renew and certificate.is_due_for_renewal(now=now):
            try:
                certificate.renew()
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception(
                    "Failed to auto-renew certificate %s: %s",
                    certificate.pk,
                    exc,
                )
            else:
                renewed += 1

    if updated or renewed:
        logger.info(
            "Certificate refresh complete: %s updated, %s renewed.",
            updated,
            renewed,
        )

    return {"updated": updated, "renewed": renewed}
