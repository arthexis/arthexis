"""Celery tasks for experimental classifier training and camera inference."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.video.models import MjpegStream

from .models import ImageClassifierModel
from .pipeline import classify_stream, train_classifier

logger = logging.getLogger(__name__)


@shared_task
def train_image_classifier_task(classifier_id: int) -> dict[str, object]:
    """Train the specified classifier model from verified examples."""

    classifier = ImageClassifierModel.objects.get(pk=classifier_id)
    training_run = train_classifier(classifier)
    return {
        "classifier_id": classifier.pk,
        "training_run_id": training_run.pk,
        "status": training_run.status,
        "sample_count": training_run.sample_count,
    }


@shared_task
def classify_camera_streams() -> dict[str, int]:
    """Run one classification pass across active camera streams."""

    classifier = ImageClassifierModel.selected_general_model()
    if classifier is None or not classifier.storage_uri:
        return {"classified_streams": 0, "prediction_records": 0, "failed_streams": 0, "skipped_streams": 0}

    classified_streams = 0
    prediction_records = 0
    failed_streams = 0
    skipped_streams = 0

    for stream in MjpegStream.objects.filter(is_active=True).select_related("video_device"):
        try:
            _media_file, records = classify_stream(stream, classifier=classifier)
        except Exception as exc:  # pragma: no cover - depends on camera runtime
            failed_streams += 1
            logger.warning("Camera classification failed for %s: %s", stream.slug, exc)
            continue
        if not records:
            skipped_streams += 1
            continue
        classified_streams += 1
        prediction_records += len(records)

    return {
        "classified_streams": classified_streams,
        "prediction_records": prediction_records,
        "failed_streams": failed_streams,
        "skipped_streams": skipped_streams,
    }

