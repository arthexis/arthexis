"""Helpers for running the release workflow outside of the admin UI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import DatabaseError, models

from .views import (
    DirtyRepository,
    PUBLISH_STEPS,
    ApprovalRequired,
    _append_log,
    _release_log_name,
    _resolve_release_log_dir,
)

logger = logging.getLogger(__name__)
_LOCK_DIR = Path("locks")


def resolve_release_severity(version: str | None) -> str:
    """Return the severity for a version, preferring active packages."""

    try:
        from apps.core.models import PackageRelease  # noqa: WPS433 - runtime import
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


def _persist_state(lock_path: Path, ctx: dict[str, Any], *, final: bool = False) -> None:
    if final:
        if lock_path.exists():
            lock_path.unlink()
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(ctx), encoding="utf-8")


def run_headless_publish(release, *, auto_release: bool = False) -> Path:
    """Execute the release workflow outside of the interactive admin view."""

    log_dir, warning = _resolve_release_log_dir(Path(settings.LOG_DIR))
    log_path = log_dir / _release_log_name(release.package.name, release.version)
    if log_path.exists():
        log_path.unlink()

    lock_path = _LOCK_DIR / f"release_publish_{release.pk}.json"
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
    _persist_state(lock_path, ctx)

    _append_log(log_path, "Scheduled release started automatically")
    if warning:
        _append_log(log_path, warning)

    for index, (name, func) in enumerate(PUBLISH_STEPS):
        try:
            func(release, ctx, log_path, user=None)
        except ApprovalRequired as exc:
            message = "Scheduled release requires manual approval"
            _append_log(log_path, message)
            ctx["error"] = message
            logger.warning("%s: %s", release, message)
            _persist_state(lock_path, ctx, final=True)
            raise ReleaseWorkflowBlocked(message, log_path=log_path) from exc
        except DirtyRepository as exc:
            message = "Scheduled release halted by dirty repository state"
            _append_log(log_path, message)
            ctx["error"] = message
            logger.warning("%s: %s", release, message)
            _persist_state(lock_path, ctx, final=True)
            raise ReleaseWorkflowBlocked(message, log_path=log_path) from exc
        except Exception as exc:  # pragma: no cover - safety net
            message = f"{name} failed: {exc}"
            _append_log(log_path, message)
            ctx["error"] = message
            logger.exception("Scheduled release %s failed", release)
            _persist_state(lock_path, ctx, final=True)
            raise ReleaseWorkflowError(message, log_path=log_path) from exc
        else:
            ctx["step"] = index + 1
            _persist_state(lock_path, ctx)

    _append_log(log_path, "Scheduled release completed")
    _persist_state(lock_path, ctx, final=True)
    return log_path
