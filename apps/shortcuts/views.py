"""Views for browser-side shortcut discovery and execution."""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from apps.features.utils import is_suite_feature_enabled

from .constants import SHORTCUT_MANAGEMENT_FEATURE_SLUG
from .models import Shortcut
from .runtime import execute_client_shortcut


@login_required
@require_GET
def client_shortcut_config(request: HttpRequest) -> JsonResponse:
    """Return active client shortcuts and pattern metadata for current user."""

    if not is_suite_feature_enabled(SHORTCUT_MANAGEMENT_FEATURE_SLUG, default=False):
        return JsonResponse({"enabled": False, "shortcuts": []})

    shortcuts = Shortcut.objects.filter(kind=Shortcut.Kind.CLIENT, is_active=True).prefetch_related(
        "clipboard_patterns"
    )
    payload = []
    for shortcut in shortcuts:
        payload.append(
            {
                "id": shortcut.pk,
                "key_combo": shortcut.key_combo,
                "use_clipboard_patterns": shortcut.use_clipboard_patterns,
                "patterns": [
                    {
                        "id": pattern.pk,
                        "pattern": pattern.pattern,
                        "priority": pattern.priority,
                    }
                    for pattern in shortcut.clipboard_patterns.filter(is_active=True).order_by("priority", "pk")
                ],
            }
        )

    return JsonResponse({"enabled": True, "shortcuts": payload})


@login_required
@require_POST
def execute_client_shortcut_view(request: HttpRequest, shortcut_id: int) -> JsonResponse:
    """Execute a client shortcut and return output targets."""

    if not is_suite_feature_enabled(SHORTCUT_MANAGEMENT_FEATURE_SLUG, default=False):
        return JsonResponse({"detail": "Shortcut management is disabled."}, status=403)

    shortcut = Shortcut.objects.filter(pk=shortcut_id, kind=Shortcut.Kind.CLIENT, is_active=True).first()
    if shortcut is None:
        return JsonResponse({"detail": "Shortcut not found."}, status=404)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON payload."}, status=400)

    clipboard = str(payload.get("clipboard") or "")
    keyboard = str(payload.get("keyboard") or "")
    try:
        execution = execute_client_shortcut(shortcut=shortcut, clipboard=clipboard, keyboard=keyboard)
    except ValidationError as exc:
        message = exc.message if hasattr(exc, "message") else "; ".join(exc.messages) or "Invalid shortcut configuration."
        return JsonResponse({"detail": message}, status=400)

    return JsonResponse(
        {
            "shortcut": shortcut.key_combo,
            "selection": execution.selection,
            "rendered_output": execution.rendered_output,
            "matched_pattern_id": execution.matched_pattern_id,
            "clipboard_output": execution.clipboard_output,
            "keyboard_output": execution.keyboard_output,
        }
    )
