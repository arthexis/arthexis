"""Helpers for running the release workflow outside of the admin UI."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import DatabaseError, models

from apps.core.views.reports.logs import (
    _append_log,
    _release_log_name,
    _resolve_release_log_dir,
)
from apps.release.domain import PUBLISH_STEPS as DOMAIN_PUBLISH_STEPS
from apps.release.publishing import DirtyRepository, PublishPending

logger = logging.getLogger(__name__)


def resolve_release_severity(version: str | None) -> str:
    """Return the severity for a version, preferring active packages."""

    try:
        from apps.release.models import PackageRelease  # noqa: WPS433 - runtime import
    except Exception:  # pragma: no cover - app registry not ready
        return "normal"

    if not version:
        return PackageRelease.Severity.NORMAL

    try:
        release = (
            PackageRelease.objects.filter(version=version)
            .select_related("package")
            .order_by(
                models.Case(
                    models.When(package__is_active=True, then=models.Value(0)),
                    default=models.Value(1),
                    output_field=models.IntegerField(),
                ),
                "-pk",
            )
            .first()
        )
    except DatabaseError:
        logger.exception("Failed to resolve package release severity")
        return PackageRelease.Severity.NORMAL

    if release:
        return release.severity
    return PackageRelease.Severity.NORMAL


class ReleaseWorkflowError(Exception):
    """Base exception for headless release execution failures."""

    def __init__(self, message: str, *, log_path: Path | None = None):
        super().__init__(message)
        self.log_path = log_path


class ReleaseWorkflowBlocked(ReleaseWorkflowError):
    """Raised when the release cannot progress without manual intervention."""


_STEP_WORKFLOW_NAME = "release_publish"


@dataclass(frozen=True)
class ReleaseWorkflowStep:
    name: str
    func: Callable


def _build_release_workflow() -> tuple[ReleaseWorkflowStep, ...]:
    from apps.release.publishing import pipeline

    return tuple(
        ReleaseWorkflowStep(name=name, func=getattr(pipeline, handler_name))
        for name, handler_name in DOMAIN_PUBLISH_STEPS
    )


def run_headless_publish(release, *, auto_release: bool = False) -> Path:
    """Execute the release workflow outside of the interactive admin view."""

    log_dir, warning = _resolve_release_log_dir(Path(settings.LOG_DIR))
    log_path = log_dir / _release_log_name(release.package.name, release.version)
    if log_path.exists():
        log_path.unlink()

    workflow = _build_release_workflow()
    ctx: dict[str, Any] = {
        "step": 0,
        "started": True,
        "paused": False,
        "auto_release": auto_release,
        "dry_run": False,
        "log": log_path.name,
    }
    if warning:
        ctx["log_dir_warning_message"] = warning

    _append_log(log_path, "Scheduled release started automatically")
    if warning:
        _append_log(log_path, warning)

    def _execute_step(step: ReleaseWorkflowStep, context: dict[str, Any]):
        try:
            return step.func(release, context, log_path, user=None)
        except DirtyRepository as exc:
            message = "Scheduled release halted by dirty repository state"
            _append_log(log_path, message)
            context["error"] = message
            logger.warning("%s: %s", release, message)
            raise ReleaseWorkflowBlocked(message, log_path=log_path) from exc
        except PublishPending as exc:
            if context.get("test_pruning_required"):
                message = "Scheduled release awaiting test pruning evidence"
            else:
                message = "Scheduled release awaiting publish completion"
            _append_log(log_path, message)
            context["error"] = message
            logger.warning("%s: %s", release, message)
            raise ReleaseWorkflowBlocked(message, log_path=log_path) from exc
        except Exception as exc:  # pragma: no cover - safety net
            message = f"{step.name} failed: {exc}"
            _append_log(log_path, message)
            context["error"] = message
            logger.exception("Scheduled release %s failed", release)
            raise ReleaseWorkflowError(message, log_path=log_path) from exc

    for step_index, step in enumerate(workflow, start=1):
        ctx["step"] = step_index
        _execute_step(step, ctx)
    _append_log(log_path, "Scheduled release completed")
    return log_path
