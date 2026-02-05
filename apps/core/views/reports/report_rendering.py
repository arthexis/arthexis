from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import get_template
from django.utils.translation import gettext as _

from .common import SENSITIVE_CONTEXT_KEYS

logger = logging.getLogger(__name__)


def _ensure_template_name(template, name: str):
    """Ensure the template has a name attribute for debugging hooks."""

    if not getattr(template, "name", None):
        template.name = name
    return template


def _render_release_progress_error(
    request,
    release,
    action: str,
    message: str,
    *,
    status: int = 400,
    debug_info: dict | None = None,
) -> HttpResponse:
    """Return a simple error response for the release progress view."""

    debug_info = debug_info or {}
    logger.error(
        "Release progress error for %s (%s): %s; debug=%s",
        release or "unknown release",
        action,
        message,
        debug_info,
    )
    debug_payload = None
    if settings.DEBUG and debug_info:
        debug_payload = json.dumps(debug_info, indent=2, sort_keys=True)
    return render(
        request,
        "core/release_progress_error.html",
        {
            "release": release,
            "action": action,
            "message": str(message),
            "debug_info": debug_payload,
            "status_code": status,
        },
        status=status,
    )


def _sanitize_release_error_message(error: str | None, ctx: dict) -> str | None:
    if not error:
        return None

    sanitized = str(error)
    for key in SENSITIVE_CONTEXT_KEYS:
        value = ctx.get(key)
        if value:
            sanitized = sanitized.replace(str(value), "[redacted]")
    return sanitized
