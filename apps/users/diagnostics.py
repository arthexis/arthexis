"""Diagnostics capture and bundle helpers for user-scoped triage."""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from .models import UserDiagnosticBundle, UserDiagnosticEvent, UserDiagnosticsProfile

logger = logging.getLogger(__name__)


def _active_profile_for_user(user) -> UserDiagnosticsProfile | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return (
        UserDiagnosticsProfile.objects.filter(user=user, is_enabled=True)
        .only("id", "collect_diagnostics", "allow_manual_feedback")
        .first()
    )


def capture_request_exception(*, request, exception: Exception) -> None:
    """Persist a diagnostics event when the current user has opted in."""

    user = getattr(request, "user", None)
    profile = _active_profile_for_user(user)
    if profile is None or not profile.collect_diagnostics:
        return

    summary = f"{type(exception).__name__}: {exception}"
    path = getattr(request, "path", "") or ""
    method = getattr(request, "method", "") or ""
    UserDiagnosticEvent.objects.create(
        user=user,
        profile=profile,
        source=UserDiagnosticEvent.Source.ERROR,
        summary=summary[:255],
        details="",
        request_method=method[:16],
        request_path=path[:500],
        status_code=getattr(exception, "status_code", None),
        fingerprint=UserDiagnosticEvent.build_fingerprint(
            source=UserDiagnosticEvent.Source.ERROR,
            summary=summary,
            request_method=method,
            request_path=path,
        ),
        metadata={
            "full_path": getattr(request, "get_full_path", lambda: path)(),
        },
    )


def create_manual_feedback(*, user, summary: str, details: str = "") -> UserDiagnosticEvent | None:
    """Create a manual feedback diagnostic event when enabled for the user."""

    profile = _active_profile_for_user(user)
    if profile is None or not profile.allow_manual_feedback:
        return None
    summary_text = (summary or "").strip()
    details_text = (details or "").strip()
    if not summary_text:
        return None
    return UserDiagnosticEvent.objects.create(
        user=user,
        profile=profile,
        source=UserDiagnosticEvent.Source.FEEDBACK,
        summary=summary_text[:255],
        details=details_text,
        fingerprint=UserDiagnosticEvent.build_fingerprint(
            source=UserDiagnosticEvent.Source.FEEDBACK,
            summary=summary_text,
        ),
    )


def build_diagnostic_bundle(*, user, title: str = "", limit: int = 50) -> UserDiagnosticBundle:
    """Create and return a diagnostics bundle for the given user."""

    profile = _active_profile_for_user(user)
    events = list(
        UserDiagnosticEvent.objects.filter(user=user)
        .select_related("profile")
        .order_by("-occurred_at", "-pk")[: max(1, limit)]
    )
    heading = title.strip() if title else ""
    if not heading:
        heading = f"Diagnostics bundle for {getattr(user, 'username', 'unknown')}"
    lines = [heading, f"Generated at: {timezone.now().isoformat()}", ""]
    for event in events:
        lines.append(
            f"- [{event.source}] {event.occurred_at.isoformat()} {event.summary}"
        )
        if event.request_method or event.request_path:
            lines.append(f"  request: {event.request_method} {event.request_path}".rstrip())
        if event.details:
            lines.append(f"  details: {event.details}")
        lines.append(f"  fingerprint: {event.fingerprint}")
    if not events:
        lines.append("- No diagnostic events available.")
    bundle = UserDiagnosticBundle.objects.create(
        user=user,
        profile=profile,
        title=heading[:200],
        report="\n".join(lines),
    )
    if events:
        bundle.events.set(events)
    return bundle


def attach_exception_signal(sender, request=None, **kwargs) -> None:
    """Signal receiver for Django's ``got_request_exception``."""

    if request is None:
        return
    exc_info = kwargs.get("exception")
    if isinstance(exc_info, Exception):
        exception = exc_info
    else:
        exception = Exception("Unknown request exception")
    try:
        capture_request_exception(request=request, exception=exception)
    except (ObjectDoesNotExist, RuntimeError, ValueError):
        logger.debug("Skipping diagnostics capture due to runtime guard.", exc_info=True)
    except Exception:
        logger.exception("Unexpected failure capturing user diagnostics.")


def build_bundle_for_username(*, username: str, title: str = "", limit: int = 50):
    user_model = get_user_model()
    user = user_model._default_manager.get(username=username)
    return build_diagnostic_bundle(user=user, title=title, limit=limit)
