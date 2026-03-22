"""HTTP endpoints for remote actions bearer API."""

from __future__ import annotations

import json

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.actions.models import RemoteAction, RemoteActionToken


def _is_json_primitive(value):
    """Return whether value is a JSON-safe primitive structure."""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return True
    if isinstance(value, list):
        return all(_is_json_primitive(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_primitive(item) for key, item in value.items())
    return False


def _resolve_bearer_token(request: HttpRequest):
    """Resolve and validate a bearer token from the Authorization header."""

    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Bearer "):
        return None, JsonResponse({"detail": "Missing Bearer token."}, status=401)

    raw_key = header.split(" ", 1)[1].strip()
    try:
        token = RemoteActionToken.authenticate_bearer(raw_key)
    except ValueError as exc:
        return None, JsonResponse({"detail": str(exc)}, status=401)
    return token, None


@require_GET
def security_groups(request: HttpRequest) -> JsonResponse:
    """List security groups for the authenticated token user."""

    token, error_response = _resolve_bearer_token(request)
    if error_response:
        return error_response

    groups = list(token.user.groups.values_list("name", flat=True).order_by("name"))
    return JsonResponse({"groups": groups})


@csrf_exempt
@require_POST
def invoke_action(request: HttpRequest, slug: str) -> JsonResponse:
    """Validate and echo a remote action invocation for an authorized bearer token."""

    token, error_response = _resolve_bearer_token(request)
    if error_response:
        return error_response

    action = RemoteAction.objects.filter(slug=slug, is_active=True).select_related("user", "group").first()
    if action is None:
        return JsonResponse({"detail": "Remote action not found."}, status=404)

    has_access = action.user_id == token.user_id or (
        action.group_id and token.user.groups.filter(pk=action.group_id).exists()
    )
    if not has_access:
        return JsonResponse({"detail": "Action is not available for this user."}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON payload."}, status=400)

    args = payload.get("args") or []
    kwargs = payload.get("kwargs") or {}
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        return JsonResponse({"detail": "Payload must contain list args and object kwargs."}, status=400)
    if not _is_json_primitive(args) or not _is_json_primitive(kwargs):
        return JsonResponse({"detail": "Payload args/kwargs must be JSON primitive values only."}, status=400)

    return JsonResponse({
        "action": action.slug,
        "args": args,
        "kwargs": kwargs,
    })
