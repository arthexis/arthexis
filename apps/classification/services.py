"""Service helpers for ingest-time classification and dispatch."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from django.utils import timezone

from .models import ClassificationTag, ContentClassification, ImageClassifierModel


def enqueue_media_for_classification(media_file) -> ContentClassification | None:
    """Create a pending classification record for newly ingested media."""

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

    ContentClassification.objects.filter(
        media_file=media_file,
        classifier=classifier,
        status=ContentClassification.Status.PENDING,
    ).update(status=ContentClassification.Status.REJECTED)

    records: list[ContentClassification] = []
    for item in predictions:
        tag_slug = str(item.get("tag", "")).strip()
        if not tag_slug:
            continue
        tag = ClassificationTag.objects.filter(slug=tag_slug, is_active=True).first()
        if tag is None:
            continue

        raw_confidence = item.get("confidence", 0)
        confidence = Decimal(str(raw_confidence))
        classification = ContentClassification.objects.create(
            media_file=media_file,
            classifier=classifier,
            tag=tag,
            confidence=confidence,
            status=ContentClassification.Status.TAGGED,
            route=tag.dispatch_route,
            metadata={"prediction": item},
        )
        if tag.auto_dispatch:
            classification.status = ContentClassification.Status.DISPATCHED
            classification.dispatched_at = timezone.now()
            classification.save(update_fields=["status", "dispatched_at"])
        records.append(classification)

    return records
