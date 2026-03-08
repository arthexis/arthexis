"""Model and service behavior tests for classifier orchestration."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.classification.models import (
    ClassificationTag,
    ContentClassification,
    ImageClassifierModel,
)
from apps.classification.services import apply_model_predictions
from apps.content.storage.models import MediaBucket, MediaFile

@pytest.mark.django_db
def test_selected_classifier_must_be_ready():
    """Selected classifiers must be in a ready state."""

    with pytest.raises(ValidationError, match="ready"):
        ImageClassifierModel.objects.create(
            slug="classifier-draft",
            name="Classifier Draft",
            version="v1",
            status=ImageClassifierModel.Status.DRAFT,
            is_selected=True,
        )

@pytest.mark.django_db
def test_selecting_classifier_demotes_previous_selection():
    """Only one selected model should remain after selecting a new classifier."""

    first = ImageClassifierModel.objects.create(
        slug="general-v1",
        name="General",
        version="v1",
        status=ImageClassifierModel.Status.READY,
        is_selected=True,
    )
    second = ImageClassifierModel.objects.create(
        slug="general-v2",
        name="General",
        version="v2",
        status=ImageClassifierModel.Status.READY,
        is_selected=True,
    )

    first.refresh_from_db()
    second.refresh_from_db()

    assert first.is_selected is False
    assert second.is_selected is True

@pytest.mark.django_db
def test_media_creation_queues_pending_classification():
    """Creating a media file should enqueue a pending classification record."""

    ImageClassifierModel.objects.create(
        slug="general-v1",
        name="General",
        version="v1",
        status=ImageClassifierModel.Status.READY,
        is_selected=True,
    )
    bucket = MediaBucket.objects.create(name="Uploads")

    media = MediaFile.objects.create(
        bucket=bucket,
        file="protocols/buckets/example/image-a.jpg",
        original_name="image-a.jpg",
        content_type="image/jpeg",
        size=123,
    )

    classification = ContentClassification.objects.get(media_file=media)
    assert classification.status == ContentClassification.Status.PENDING

@pytest.mark.django_db
def test_media_creation_does_not_queue_non_image_file():
    """Non-image media should not be enqueued for image classification."""

    ImageClassifierModel.objects.create(
        slug="general-v1",
        name="General",
        version="v1",
        status=ImageClassifierModel.Status.READY,
        is_selected=True,
    )
    bucket = MediaBucket.objects.create(name="Uploads")

    media = MediaFile.objects.create(
        bucket=bucket,
        file="protocols/buckets/example/doc-a.pdf",
        original_name="doc-a.pdf",
        content_type="application/pdf",
        size=123,
    )

    assert ContentClassification.objects.filter(media_file=media).count() == 0

@pytest.mark.django_db
def test_apply_predictions_sets_dispatch_state_from_tag():
    """Prediction processing should mark dispatch status based on tag policy."""

    classifier = ImageClassifierModel.objects.create(
        slug="general-v1",
        name="General",
        version="v1",
        status=ImageClassifierModel.Status.READY,
        is_selected=True,
    )
    auto_tag = ClassificationTag.objects.create(
        slug="sensitive",
        name="Sensitive",
        auto_dispatch=True,
        dispatch_route="security.review",
    )
    manual_tag = ClassificationTag.objects.create(slug="safe", name="Safe", auto_dispatch=False)

    bucket = MediaBucket.objects.create(name="Uploads")
    media = MediaFile.objects.create(
        bucket=bucket,
        file="protocols/buckets/example/image-b.jpg",
        original_name="image-b.jpg",
        content_type="image/jpeg",
        size=321,
    )

    predictions = [
        {"tag": auto_tag.slug, "confidence": "0.91"},
        {"tag": manual_tag.slug, "confidence": "0.62"},
    ]
    records = apply_model_predictions(media, predictions, classifier=classifier)

    assert len(records) == 2
    dispatched = ContentClassification.objects.get(media_file=media, tag=auto_tag)
    manual = ContentClassification.objects.get(media_file=media, tag=manual_tag)

    assert dispatched.status == ContentClassification.Status.DISPATCHED
    assert dispatched.route == "security.review"
    assert dispatched.confidence == Decimal("0.9100")
    assert manual.status == ContentClassification.Status.TAGGED
    assert dispatched.classified_at is not None
    assert manual.classified_at is not None

@pytest.mark.django_db
def test_apply_predictions_skips_invalid_confidence_values():
    """Malformed confidence values should be ignored without failing the batch."""

    classifier = ImageClassifierModel.objects.create(
        slug="general-v1",
        name="General",
        version="v1",
        status=ImageClassifierModel.Status.READY,
        is_selected=True,
    )
    tag = ClassificationTag.objects.create(slug="safe", name="Safe")

    bucket = MediaBucket.objects.create(name="Uploads")
    media = MediaFile.objects.create(
        bucket=bucket,
        file="protocols/buckets/example/image-c.jpg",
        original_name="image-c.jpg",
        content_type="image/jpeg",
        size=111,
    )

    records = apply_model_predictions(
        media,
        [
            {"tag": tag.slug, "confidence": "not-a-number"},
            {"tag": tag.slug, "confidence": "0.80"},
        ],
        classifier=classifier,
    )

    assert len(records) == 1
    assert records[0].confidence == Decimal("0.8000")
