from __future__ import annotations

import logging

from celery import shared_task
from django.db import DatabaseError

from apps.release import release_workflow


logger = logging.getLogger(__name__)

SEVERITY_NORMAL = "normal"
SEVERITY_LOW = "low"
SEVERITY_CRITICAL = "critical"

_PackageReleaseModel = None


def _get_package_release_model():
    """Return the :class:`release.models.PackageRelease` model when available."""

    global _PackageReleaseModel

    if _PackageReleaseModel is not None:
        return _PackageReleaseModel

    try:
        from apps.release.models import PackageRelease  # noqa: WPS433 - runtime import
    except Exception:  # pragma: no cover - app registry not ready
        return None

    _PackageReleaseModel = PackageRelease
    return PackageRelease


model = _get_package_release_model()
if model is not None:  # pragma: no branch - runtime constant setup
    SEVERITY_NORMAL = model.Severity.NORMAL
    SEVERITY_LOW = model.Severity.LOW
    SEVERITY_CRITICAL = model.Severity.CRITICAL


def _resolve_release_severity(version: str | None) -> str:
    try:
        return release_workflow.resolve_release_severity(version)
    except Exception:  # pragma: no cover - protective fallback
        logger.exception("Failed to resolve release severity")
        return SEVERITY_NORMAL


def _latest_release() -> tuple[str | None, str | None]:
    """Return the latest release version and revision when available."""

    model = _get_package_release_model()
    if model is None:
        return None, None

    try:
        release = model.latest()
    except DatabaseError:  # pragma: no cover - depends on DB availability
        return None, None
    except Exception:  # pragma: no cover - defensive catch-all
        return None, None

    if not release:
        return None, None

    version = getattr(release, "version", None)
    revision = getattr(release, "revision", None)
    return version, revision


def execute_scheduled_release(release_id: int) -> None:
    """Run the automated release flow for a scheduled PackageRelease."""

    model = _get_package_release_model()
    if model is None:
        logger.warning("Scheduled release %s skipped: model unavailable", release_id)
        return

    release = model.objects.filter(pk=release_id).first()
    if release is None:
        logger.warning("Scheduled release %s skipped: release not found", release_id)
        return

    try:
        release_workflow.run_headless_publish(release, auto_release=True)
    finally:
        release.clear_schedule(save=True)


@shared_task
def run_scheduled_release(release_id: int) -> None:
    """Entrypoint used by django-celery-beat to trigger scheduled releases."""

    execute_scheduled_release(release_id)


@shared_task(name="apps.core.tasks.run_scheduled_release")
def legacy_run_scheduled_release(release_id: int) -> None:
    """Backward-compatible alias for the scheduled release task."""

    run_scheduled_release(release_id)
