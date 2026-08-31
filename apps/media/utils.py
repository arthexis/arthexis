from __future__ import annotations

import mimetypes
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext_lazy as _

from .models import MediaBucket, MediaFile, MediaSourceFile, file_sha256


def guess_content_type(
    filename: str, *, default: str = "application/octet-stream"
) -> str:
    guessed_type, _encoding = mimetypes.guess_type(filename)
    return (guessed_type or default).strip() or default


def _rewind(file_obj) -> None:
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)


def _sha256_for_file(file_obj) -> str:
    return file_sha256(file_obj)


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
        raise ValidationError(
            {"file": _("File exceeds the allowed size for this bucket.")}
        )

    media_file = MediaFile(
        bucket=bucket,
        file=uploaded_file,
        original_name=original_name or filename,
        content_type=content_type or getattr(uploaded_file, "content_type", "") or "",
        size=size_value or 0,
    )
    media_file.save()
    return media_file


def create_media_source_file(
    *,
    uploaded_file,
    name: str | None = None,
    source_type: str = MediaSourceFile.SourceType.MSE_SET,
    source_uri: str = "",
    original_name: str | None = None,
    content_type: str | None = None,
    size: int | None = None,
    checksum_sha256: str | None = None,
) -> MediaSourceFile:
    filename = original_name or getattr(uploaded_file, "name", "")
    if not filename:
        raise ValidationError({"file": _("Source file name is required.")})

    size_value = size
    if size_value is None:
        size_value = getattr(uploaded_file, "size", 0) or 0
    checksum_value = checksum_sha256 or _sha256_for_file(uploaded_file)
    media_source = MediaSourceFile(
        name=name or Path(filename).stem,
        source_type=source_type,
        original_name=filename,
        content_type=content_type
        or getattr(uploaded_file, "content_type", "")
        or guess_content_type(filename),
        size=size_value or 0,
        checksum_sha256=checksum_value,
        source_uri=source_uri,
    )
    _rewind(uploaded_file)
    media_source.file.save(Path(filename).name, uploaded_file, save=False)
    media_source.save()
    return media_source


def copy_media_source_file_from_path(
    path: str | Path,
    *,
    name: str | None = None,
    source_type: str = MediaSourceFile.SourceType.MSE_SET,
    source_uri: str | None = None,
    content_type: str | None = None,
) -> MediaSourceFile:
    source_path = Path(path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    if not source_path.is_file():
        raise IsADirectoryError(source_path)

    with source_path.open("rb") as handle:
        uploaded_file = File(handle, name=source_path.name)
        return create_media_source_file(
            uploaded_file=uploaded_file,
            name=name,
            source_type=source_type,
            source_uri=source_uri if source_uri is not None else source_path.as_posix(),
            original_name=source_path.name,
            content_type=content_type or guess_content_type(source_path.name),
            size=source_path.stat().st_size,
        )
