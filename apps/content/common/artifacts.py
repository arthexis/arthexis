"""Helpers for artifact path resolution and metadata persistence."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings


def resolve_sample_path(path: str | Path) -> Path:
    """Resolve an artifact path into an absolute filesystem location."""

    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = settings.LOG_DIR / file_path
    return file_path


def update_or_create_artifact(*, model_cls, lookup: dict[str, object], metadata: dict[str, object]):
    """Persist metadata for an artifact model with idempotent field updates."""

    artifact, created = model_cls.objects.get_or_create(**lookup, defaults=metadata)
    if created:
        return artifact

    update_fields: list[str] = []
    for field, value in metadata.items():
        if getattr(artifact, field) != value:
            setattr(artifact, field, value)
            update_fields.append(field)
    if update_fields:
        artifact.save(update_fields=update_fields)
    return artifact
