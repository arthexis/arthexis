"""Migration-safe fallbacks for media utilities."""

import uuid
from pathlib import Path


def media_bucket_slug() -> str:
    """Fallback slug generator when the main model module is unavailable."""

    return uuid.uuid4().hex


def media_file_path(instance: object, filename: str) -> str:
    """Fallback upload path for migration-time file fields."""

    bucket_slug = getattr(getattr(instance, "bucket", None), "slug", None) or "bucket"
    return f"protocols/buckets/{bucket_slug}/{Path(filename).name}"
