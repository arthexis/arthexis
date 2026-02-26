"""Idempotent release data transforms with persisted progress checkpoints."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransformResult:
    """Summary for a single transform execution."""

    updated: int
    processed: int
    complete: bool


TransformRunner = Callable[[dict[str, object]], TransformResult]


def _checkpoint_dir(base_dir: Path | None = None) -> Path:
    """Return the checkpoint directory used by deferred transforms."""

    root = base_dir or Path(settings.BASE_DIR)
    target = root / ".release-transforms"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _load_checkpoint(name: str, *, base_dir: Path | None = None) -> dict[str, object]:
    """Load checkpoint payload for a transform if it exists."""

    path = _checkpoint_dir(base_dir) / f"{name}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Invalid checkpoint payload for transform %s", name)
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _store_checkpoint(name: str, payload: dict[str, object], *, base_dir: Path | None = None) -> None:
    """Persist checkpoint payload for a transform."""

    path = _checkpoint_dir(base_dir) / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_package_release_version_normalization(checkpoint: dict[str, object]) -> TransformResult:
    """Normalize package release versions in bounded batches."""

    from apps.release.models import PackageRelease

    raw_pk = checkpoint.get("last_pk", 0)
    try:
        last_pk = int(raw_pk)
    except (TypeError, ValueError):
        last_pk = 0

    batch_size = 100
    updated = 0
    processed = 0

    queryset = (
        PackageRelease.objects.filter(pk__gt=last_pk)
        .order_by("pk")
        .only("pk", "version")[:batch_size]
    )
    items = list(queryset)
    if not items:
        return TransformResult(updated=0, processed=0, complete=True)

    with transaction.atomic():
        for release in items:
            processed += 1
            normalized = PackageRelease.normalize_version(release.version)
            if normalized != release.version:
                release.version = normalized
                release.save(update_fields=["version"])
                updated += 1

    checkpoint["last_pk"] = items[-1].pk
    return TransformResult(updated=updated, processed=processed, complete=False)


TRANSFORMS: dict[str, TransformRunner] = {
    "release.normalize_package_release_versions": _run_package_release_version_normalization,
}


def list_transform_names() -> list[str]:
    """Return registered transform names in deterministic order."""

    return sorted(TRANSFORMS.keys())


def run_transform(name: str, *, base_dir: Path | None = None) -> TransformResult:
    """Execute one transform and persist checkpoint state."""

    runner = TRANSFORMS.get(name)
    if runner is None:
        raise KeyError(f"Unknown release transform: {name}")

    checkpoint = _load_checkpoint(name, base_dir=base_dir)
    result = runner(checkpoint)
    checkpoint["complete"] = result.complete
    _store_checkpoint(name, checkpoint, base_dir=base_dir)
    return result

