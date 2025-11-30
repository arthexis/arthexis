from __future__ import annotations

import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.db import models
from django.utils import timezone


logger = logging.getLogger(__name__)


@shared_task
def validate_reference_links() -> int:
    """Validate stale or missing reference URLs and store their status codes."""

    from .models import Reference

    now = timezone.now()
    cutoff = now - timedelta(days=7)
    references = Reference.objects.filter(
        models.Q(validated_url_at__isnull=True)
        | models.Q(validated_url_at__lt=cutoff)
    ).exclude(value="")

    updated = 0
    for reference in references:
        status_code: int | None = None
        try:
            response = requests.get(reference.value, timeout=5)
        except requests.RequestException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "Failed to validate reference %s at %s", reference.pk, reference.value
            )
            logger.debug("Reference validation error", exc_info=exc)
        else:
            status_code = response.status_code

        reference.validation_status = status_code if status_code is not None else 0
        reference.validated_url_at = now
        reference.save(update_fields=["validation_status", "validated_url_at"])
        updated += 1

    return updated
