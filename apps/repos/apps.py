import hashlib
import logging
import time
import traceback
from contextlib import suppress
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.core.signals import got_request_exception

from apps.celery.utils import enqueue_task
from apps.tasks.tasks import report_exception_to_github


logger = logging.getLogger(__name__)


class ReposConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.repos"
    label = "repos"

    def ready(self):  # pragma: no cover - called by Django
        _configure_github_issue_reporting()


def _configure_github_issue_reporting():
    def queue_github_issue(sender, request=None, **kwargs):
        if not getattr(settings, "GITHUB_ISSUE_REPORTING_ENABLED", True):
            return
        if request is None:
            return

        exception = kwargs.get("exception")
        if exception is None:
            return

        try:
            tb_exc = traceback.TracebackException.from_exception(exception)
            stack = tb_exc.stack
            top_frame = stack[-1] if stack else None
            fingerprint_parts = [
                exception.__class__.__module__,
                exception.__class__.__name__,
            ]
            if top_frame:
                fingerprint_parts.extend(
                    [
                        top_frame.filename,
                        str(top_frame.lineno),
                        top_frame.name,
                    ]
                )
            fingerprint = hashlib.sha256(
                "|".join(fingerprint_parts).encode("utf-8")
            ).hexdigest()

            cooldown = getattr(settings, "GITHUB_ISSUE_REPORTING_COOLDOWN", 3600)
            lock_dir = Path(settings.BASE_DIR) / ".locks" / "github-issues"
            fingerprint_path = None
            now = time.time()

            with suppress(OSError):
                lock_dir.mkdir(parents=True, exist_ok=True)
                fingerprint_path = lock_dir / fingerprint
                if fingerprint_path.exists():
                    age = now - fingerprint_path.stat().st_mtime
                    if age < cooldown:
                        return

            if fingerprint_path is not None:
                with suppress(OSError):
                    fingerprint_path.write_text(str(now))

            user_repr = None
            user = getattr(request, "user", None)
            if user is not None:
                try:
                    if getattr(user, "is_authenticated", False):
                        user_repr = user.get_username()
                    else:
                        user_repr = "anonymous"
                except Exception:  # pragma: no cover - defensive
                    user_repr = str(user)

            payload = {
                "path": getattr(request, "path", None),
                "method": getattr(request, "method", None),
                "user": user_repr,
                "active_app": getattr(request, "active_app", None),
                "fingerprint": fingerprint,
                "exception_class": f"{exception.__class__.__module__}.{exception.__class__.__name__}",
                "traceback": "".join(tb_exc.format()),
            }

            enqueue_task(report_exception_to_github, payload)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to queue GitHub issue from request exception")

    got_request_exception.connect(
        queue_github_issue,
        dispatch_uid="apps.repos.github_issue_reporter",
        weak=False,
    )
