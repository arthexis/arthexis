from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext_lazy as _

from .models import MediaBucket, MediaFile


def ensure_media_bucket(
    *,
    slug: str,
    name: str,
    allowed_patterns: str = "",
    max_bytes: int | None = None,
    expires_at=None,
) -> MediaBucket:
    bucket, created = MediaBucket.objects.get_or_create(
        slug=slug,
        defaults={
            "name": name,
            "allowed_patterns": allowed_patterns,
            "max_bytes": max_bytes,
            "expires_at": expires_at,
        },
    )
    if created:
        return bucket

    updates = {}
    if bucket.name != name:
        updates["name"] = name
    if bucket.allowed_patterns != allowed_patterns:
        updates["allowed_patterns"] = allowed_patterns
    if bucket.max_bytes != max_bytes:
        updates["max_bytes"] = max_bytes
    if bucket.expires_at != expires_at:
        updates["expires_at"] = expires_at
    if updates:
        MediaBucket.objects.filter(pk=bucket.pk).update(**updates)
        bucket.refresh_from_db()
    return bucket


def create_media_file(
    *,
    bucket: MediaBucket,
    uploaded_file: UploadedFile,
    original_name: str | None = None,
    content_type: str | None = None,
    size: int | None = None,
) -> MediaFile:
    filename = original_name or getattr(uploaded_file, "name", "")
    if not bucket.allows_filename(filename):
        raise ValidationError({"file": _("File type is not allowed for this bucket.")})

    size_value = size
    if size_value is None:
        size_value = getattr(uploaded_file, "size", 0) or 0
    if not bucket.allows_size(size_value):
        raise ValidationError({"file": _("File exceeds the allowed size for this bucket.")})

    media_file = MediaFile(
        bucket=bucket,
        file=uploaded_file,
        original_name=original_name or filename,
        content_type=content_type or getattr(uploaded_file, "content_type", "") or "",
        size=size_value or 0,
    )
    media_file.save()
    return media_file
