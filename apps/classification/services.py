"""Service helpers for ingest-time classification and dispatch."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import ClassificationTag, ContentClassification, ImageClassifierModel


def enqueue_media_for_classification(media_file) -> ContentClassification | None:
    """Create a pending classification record for newly ingested media."""

    if not (media_file.content_type or "").startswith("image/"):
        return None

    classifier = ImageClassifierModel.selected_general_model()
    if classifier is None:
        return None
    return ContentClassification.objects.create(
        media_file=media_file,
        classifier=classifier,
        status=ContentClassification.Status.PENDING,
    )


def apply_model_predictions(
    media_file,
    predictions: Sequence[dict[str, object]],
    *,
    classifier: ImageClassifierModel | None = None,
) -> list[ContentClassification]:
    """Persist model predictions and mark auto-dispatched classifications."""

    classifier = classifier or ImageClassifierModel.selected_general_model()
    if classifier is None:
        return []

    tag_slugs = {str(item.get("tag", "")).strip() for item in predictions if item.get("tag")}
    tags_by_slug = {
        tag.slug: tag
        for tag in ClassificationTag.objects.filter(slug__in=tag_slugs, is_active=True)
    }

    now = timezone.now()
    pending_records: list[ContentClassification] = []
    for item in predictions:
        tag_slug = str(item.get("tag", "")).strip()
        if not tag_slug:
            continue

        tag = tags_by_slug.get(tag_slug)
        if tag is None:
            continue

        raw_confidence = item.get("confidence", 0)
        try:
            confidence = Decimal(str(raw_confidence))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if confidence.is_nan() or confidence < Decimal("0") or confidence > Decimal("1"):
            continue

        pending_records.append(
            ContentClassification(
                media_file=media_file,
                classifier=classifier,
                tag=tag,
                confidence=confidence,
                status=(
                    ContentClassification.Status.DISPATCHED
                    if tag.auto_dispatch
                    else ContentClassification.Status.TAGGED
                ),
                route=tag.dispatch_route,
                dispatched_at=now if tag.auto_dispatch else None,
                classified_at=now,
                metadata={"prediction": item},
            )
        )

    with transaction.atomic():
        ContentClassification.objects.filter(
            media_file=media_file,
            classifier=classifier,
            status=ContentClassification.Status.PENDING,
        ).update(status=ContentClassification.Status.REJECTED)

        created_records = ContentClassification.objects.bulk_create(pending_records)

    return created_records
