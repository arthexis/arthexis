"""Experimental training and camera classification pipeline helpers."""

from __future__ import annotations

from django.utils import timezone

from .backends import resolve_backend
from .camera import capture_stream_to_media_file
from .models import ImageClassifierModel, TrainingRun, TrainingSample
from .services import apply_model_predictions


def _verified_training_samples():
    return TrainingSample.objects.select_related("media_file", "tag").filter(is_verified=True)


def train_classifier(
    classifier: ImageClassifierModel,
    *,
    initiated_by=None,
) -> TrainingRun:
    """Train ``classifier`` from the verified examples currently in the suite."""

    samples = list(_verified_training_samples())
    if not samples:
        raise ValueError("No verified training samples are available.")

    backend = resolve_backend(classifier)
    classifier.status = ImageClassifierModel.Status.TRAINING
    classifier.save(update_fields=["status"])

    training_run = TrainingRun.objects.create(
        classifier=classifier,
        status=TrainingRun.Status.RUNNING,
        initiated_by=initiated_by,
        started_at=timezone.now(),
        sample_count=len(samples),
    )

    try:
        artifact = backend.train(classifier=classifier, samples=samples)
    except Exception as exc:
        classifier.status = ImageClassifierModel.Status.FAILED
        classifier.save(update_fields=["status"])
        training_run.status = TrainingRun.Status.FAILED
        training_run.finished_at = timezone.now()
        training_run.notes = str(exc)
        training_run.save(update_fields=["status", "finished_at", "notes"])
        raise

    trained_at = timezone.now()
    classifier.storage_uri = artifact.storage_uri
    classifier.metrics = artifact.metrics
    classifier.training_parameters = {
        **(classifier.training_parameters or {}),
        "backend": artifact.backend,
    }
    classifier.status = ImageClassifierModel.Status.READY
    classifier.trained_at = trained_at
    classifier.save(
        update_fields=[
            "storage_uri",
            "metrics",
            "training_parameters",
            "status",
            "trained_at",
        ]
    )

    training_run.status = TrainingRun.Status.SUCCEEDED
    training_run.finished_at = trained_at
    training_run.metrics = artifact.metrics
    training_run.save(update_fields=["status", "finished_at", "metrics"])
    return training_run


def build_predictions_for_media_file(
    media_file,
    *,
    classifier: ImageClassifierModel | None = None,
) -> tuple[ImageClassifierModel | None, list[dict[str, object]]]:
    """Return raw predictions for ``media_file`` without persisting them."""

    classifier = classifier or ImageClassifierModel.selected_general_model()
    if classifier is None or not classifier.storage_uri:
        return classifier, []
    backend = resolve_backend(classifier)
    return classifier, backend.predict(classifier=classifier, media_file=media_file)


def predict_media_file(
    media_file,
    *,
    classifier: ImageClassifierModel | None = None,
):
    """Score ``media_file`` and persist the resulting classifications."""

    classifier, predictions = build_predictions_for_media_file(media_file, classifier=classifier)
    if classifier is None or not predictions:
        return []
    return apply_model_predictions(media_file, predictions, classifier=classifier)


def classify_stream(
    stream,
    *,
    classifier: ImageClassifierModel | None = None,
):
    """Capture a stream frame, persist it, and classify it."""

    media_file, source = capture_stream_to_media_file(stream)
    if media_file is None:
        return None, []

    classifier, predictions = build_predictions_for_media_file(media_file, classifier=classifier)
    if classifier is None or not predictions:
        return media_file, []

    for prediction in predictions:
        metadata = dict(prediction.get("metadata") or {})
        metadata.update(
            {
                "camera_stream": stream.slug,
                "frame_source": source,
            }
        )
        prediction["metadata"] = metadata
    return media_file, apply_model_predictions(media_file, predictions, classifier=classifier)

