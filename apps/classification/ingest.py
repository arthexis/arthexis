"""Helpers for importing image artifacts into the classification media pipeline."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from django.core.files.base import ContentFile

from apps.media.utils import ensure_media_bucket

SUPPORTED_IMAGE_PATTERNS = "\n".join(
    [
        "*.jpg",
        "*.jpeg",
        "*.png",
        "*.webp",
        "*.gif",
        "*.bmp",
        "*.tif",
        "*.tiff",
        "*.avif",
        "*.heic",
        "*.heif",
    ]
)


def guess_image_content_type(path: Path, *, default: str = "image/png") -> str:
    """Return the best-effort MIME type for an image path."""

    guessed_type, _ = mimetypes.guess_type(path.name)
    return (guessed_type or default).strip() or default


def create_media_file_from_bytes(
    file_bytes: bytes,
    *,
    bucket_slug: str,
    bucket_name: str,
    original_name: str,
    content_type: str = "image/png",
    queue_for_classification: bool = True,
):
    """Persist image bytes as a `MediaFile`.

    When ``queue_for_classification`` is ``False``, defer the image content type
    until after the initial insert so the generic ingest signal does not create a
    pending classifier row for operator-curated training images.
    """

    from apps.media.models import MediaFile

    bucket = ensure_media_bucket(
        slug=bucket_slug,
        name=bucket_name,
        allowed_patterns=SUPPORTED_IMAGE_PATTERNS,
    )
    initial_content_type = content_type if queue_for_classification else ""
    media_file = MediaFile(
        bucket=bucket,
        original_name=original_name,
        content_type=initial_content_type,
        size=len(file_bytes),
    )
    media_file.file.save(Path(original_name).name, ContentFile(file_bytes), save=False)
    media_file.save()
    if not queue_for_classification and media_file.content_type != content_type:
        media_file.content_type = content_type
        media_file.save(update_fields=["content_type"])
    return media_file


def create_media_file_from_path(
    path: Path,
    *,
    bucket_slug: str,
    bucket_name: str,
    original_name: str | None = None,
    queue_for_classification: bool = True,
):
    """Persist an existing image path as a `MediaFile`."""

    image_path = Path(path)
    file_bytes = image_path.read_bytes()
    name = original_name or image_path.name
    return create_media_file_from_bytes(
        file_bytes,
        bucket_slug=bucket_slug,
        bucket_name=bucket_name,
        original_name=name,
        content_type=guess_image_content_type(image_path),
        queue_for_classification=queue_for_classification,
    )
