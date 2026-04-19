"""Tests for the experimental classifier training and camera loop helpers."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.classification.camera import capture_stream_to_media_file
from apps.classification.models import (
    ClassificationTag,
    ContentClassification,
    ImageClassifierModel,
    TrainingSample,
)
from apps.classification.pipeline import classify_stream, predict_media_file, train_classifier
from apps.media.utils import create_media_file, ensure_media_bucket


def _uploaded_image(name: str, color: tuple[int, int, int]) -> SimpleUploadedFile:
    image = Image.new("RGB", (32, 32), color=color)
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type="image/jpeg",
    )


def test_train_classifier_and_predict_media_file(db):
    """Verified examples can train and score a new image."""

    bucket = ensure_media_bucket(slug="training-images", name="Training Images")
    red_media = create_media_file(
        bucket=bucket,
        uploaded_file=_uploaded_image("red.jpg", (220, 20, 20)),
    )
    blue_media = create_media_file(
        bucket=bucket,
        uploaded_file=_uploaded_image("blue.jpg", (20, 20, 220)),
    )
    target_media = create_media_file(
        bucket=bucket,
        uploaded_file=_uploaded_image("red-target.jpg", (210, 25, 25)),
    )

    red_tag = ClassificationTag.objects.create(slug="red-pattern", name="Red Pattern")
    blue_tag = ClassificationTag.objects.create(slug="blue-pattern", name="Blue Pattern")
    classifier = ImageClassifierModel.objects.create(
        slug="prototype-classifier",
        name="Prototype Classifier",
        version="v1",
        training_parameters={"backend": "color_histogram"},
    )

    TrainingSample.objects.create(media_file=red_media, tag=red_tag, is_verified=True)
    TrainingSample.objects.create(media_file=blue_media, tag=blue_tag, is_verified=True)

    training_run = train_classifier(classifier)
    records = predict_media_file(target_media, classifier=classifier)

    assert training_run.status == training_run.Status.SUCCEEDED
    assert classifier.storage_uri
    assert records
    assert records[0].tag == red_tag


def test_train_classifier_sanitizes_artifact_version_path(db):
    """Classifier artifact filenames must not include traversal segments."""

    bucket = ensure_media_bucket(slug="training-images", name="Training Images")
    media = create_media_file(
        bucket=bucket,
        uploaded_file=_uploaded_image("green.jpg", (20, 220, 20)),
    )
    tag = ClassificationTag.objects.create(slug="green-pattern", name="Green Pattern")
    classifier = ImageClassifierModel.objects.create(
        slug="prototype-classifier-paths",
        name="Prototype Classifier Paths",
        version="../outside",
        training_parameters={"backend": "color_histogram"},
    )
    TrainingSample.objects.create(media_file=media, tag=tag, is_verified=True)

    training_run = train_classifier(classifier)

    assert training_run.status == training_run.Status.SUCCEEDED
    assert classifier.storage_uri is not None
    assert classifier.storage_uri.endswith("/outside.json")
    assert "/../" not in classifier.storage_uri


def test_train_classifier_reselects_model_after_training(db):
    """Training a selected classifier should complete and restore its selection."""

    bucket = ensure_media_bucket(slug="training-images", name="Training Images")
    media = create_media_file(
        bucket=bucket,
        uploaded_file=_uploaded_image("green.jpg", (20, 220, 20)),
    )
    tag = ClassificationTag.objects.create(slug="green-pattern", name="Green Pattern")
    classifier = ImageClassifierModel.objects.create(
        slug="selected-prototype-classifier",
        name="Selected Prototype Classifier",
        version="v2",
        status=ImageClassifierModel.Status.READY,
        is_selected=True,
        training_parameters={"backend": "color_histogram"},
    )
    fallback_classifier = ImageClassifierModel.objects.create(
        slug="fallback-prototype-classifier",
        name="Fallback Prototype Classifier",
        version="v1",
        status=ImageClassifierModel.Status.READY,
        storage_uri="artifacts/classification/v1/model.json",
        training_parameters={"backend": "color_histogram"},
    )
    TrainingSample.objects.create(media_file=media, tag=tag, is_verified=True)

    training_run = train_classifier(classifier)
    classifier.refresh_from_db()
    fallback_classifier.refresh_from_db()

    assert training_run.status == training_run.Status.SUCCEEDED
    assert classifier.status == ImageClassifierModel.Status.READY
    assert classifier.is_selected is True
    assert fallback_classifier.is_selected is False


def test_train_classifier_requires_ready_fallback_for_selected_model(db):
    """Selected model retraining should fail without another ready classifier."""

    bucket = ensure_media_bucket(slug="training-images", name="Training Images")
    media = create_media_file(
        bucket=bucket,
        uploaded_file=_uploaded_image("green.jpg", (20, 220, 20)),
    )
    tag = ClassificationTag.objects.create(slug="green-pattern", name="Green Pattern")
    classifier = ImageClassifierModel.objects.create(
        slug="selected-without-fallback",
        name="Selected Without Fallback",
        version="v1",
        status=ImageClassifierModel.Status.READY,
        is_selected=True,
        training_parameters={"backend": "color_histogram"},
    )
    TrainingSample.objects.create(media_file=media, tag=tag, is_verified=True)

    try:
        train_classifier(classifier)
        raise AssertionError("Expected selected classifier retraining without fallback to fail.")
    except ValueError as exc:
        assert "another ready classifier" in str(exc)


def test_train_classifier_requires_artifact_backed_fallback_for_selected_model(db):
    """Selected model retraining should fail when fallback has no artifact URI."""

    bucket = ensure_media_bucket(slug="training-images", name="Training Images")
    media = create_media_file(
        bucket=bucket,
        uploaded_file=_uploaded_image("green.jpg", (20, 220, 20)),
    )
    tag = ClassificationTag.objects.create(slug="green-pattern", name="Green Pattern")
    classifier = ImageClassifierModel.objects.create(
        slug="selected-with-nonrunnable-fallback",
        name="Selected With Nonrunnable Fallback",
        version="v1",
        status=ImageClassifierModel.Status.READY,
        is_selected=True,
        storage_uri="artifacts/classification/v1/model.json",
        training_parameters={"backend": "color_histogram"},
    )
    ImageClassifierModel.objects.create(
        slug="fallback-without-artifact",
        name="Fallback Without Artifact",
        version="v0",
        status=ImageClassifierModel.Status.READY,
        storage_uri="",
        training_parameters={"backend": "color_histogram"},
    )
    TrainingSample.objects.create(media_file=media, tag=tag, is_verified=True)

    try:
        train_classifier(classifier)
        raise AssertionError("Expected retraining to fail when fallback has no artifact.")
    except ValueError as exc:
        assert "another ready classifier" in str(exc)


def test_capture_stream_to_media_file_creates_camera_media(db):
    """A camera frame can be mirrored into the media pipeline."""

    frame_bytes = _uploaded_image("frame.jpg", (30, 200, 30)).read()
    stream = SimpleNamespace(
        slug="front-door",
        capture_frame_bytes=lambda: frame_bytes,
    )

    media_file, source = capture_stream_to_media_file(stream)

    assert media_file is not None
    assert media_file.bucket.slug == "camera-classification"
    assert source == "direct-capture"


def test_capture_stream_to_media_file_does_not_create_pending_classification(db):
    """Camera captures should not auto-queue pending rows for selected models."""

    ImageClassifierModel.objects.create(
        slug="selected-general",
        name="Selected General",
        version="v1",
        status=ImageClassifierModel.Status.READY,
        is_selected=True,
    )
    frame_bytes = _uploaded_image("frame.jpg", (30, 200, 30)).read()
    stream = SimpleNamespace(
        slug="front-door",
        capture_frame_bytes=lambda: frame_bytes,
    )

    media_file, _source = capture_stream_to_media_file(stream)

    assert media_file is not None
    assert (
        ContentClassification.objects.filter(
            media_file=media_file,
            status=ContentClassification.Status.PENDING,
        ).count()
        == 0
    )


def test_classify_stream_skips_capture_without_usable_classifier(db, monkeypatch):
    """Stream classification should not capture frames when no model can predict."""

    stream = SimpleNamespace(slug="front-door")
    captured = {"called": False}

    def _unexpected_capture(_stream):
        captured["called"] = True
        return None, None

    monkeypatch.setattr("apps.classification.pipeline.capture_stream_to_media_file", _unexpected_capture)

    media_file, records = classify_stream(stream)

    assert media_file is None
    assert records == []
    assert captured["called"] is False
