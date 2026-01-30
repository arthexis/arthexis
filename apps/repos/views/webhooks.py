"""Webhook endpoints for repository integrations."""

from __future__ import annotations

import json
from urllib.parse import parse_qs
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.repos.models.events import GitHubEvent
from apps.repos.models.repositories import GitHubRepository


def _extract_payload(request: HttpRequest) -> tuple[dict[str, Any], str]:
    raw_bytes = request.body or b""
    raw_body = raw_bytes.decode("utf-8", errors="replace")
    payload: dict[str, Any] = {}

    payload_field = request.POST.get("payload")
    if payload_field is None and raw_body and request.content_type.startswith(
        "application/x-www-form-urlencoded"
    ):
        payload_values = parse_qs(raw_body, keep_blank_values=True).get("payload")
        if payload_values:
            payload_field = payload_values[0]
    if payload_field:
        try:
            parsed = json.loads(payload_field)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed, raw_body
        if isinstance(parsed, list):
            return {"items": parsed}, raw_body

    if not raw_body:
        return payload, raw_body

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        payload = parsed
    elif isinstance(parsed, list):
        payload = {"items": parsed}
    return payload, raw_body


def _extract_repository(payload: dict[str, Any]) -> tuple[str, str]:
    repository = payload.get("repository") if isinstance(payload, dict) else None
    if isinstance(repository, dict):
        owner_info = repository.get("owner")
        owner_name = owner_info.get("login", "") if isinstance(owner_info, dict) else ""
        name = repository.get("name") or ""
        if not owner_name:
            full_name = repository.get("full_name") or ""
            if "/" in full_name:
                owner_name, name = full_name.split("/", 1)
        return str(owner_name), str(name)
    return "", ""


@csrf_exempt
def github_webhook(
    request: HttpRequest,
    owner: str = "",
    name: str = "",
    app_slug: str = "",
) -> JsonResponse:
    payload, raw_body = _extract_payload(request)
    payload_owner, payload_name = _extract_repository(payload)

    owner = owner or payload_owner
    name = name or payload_name

    repository = None
    if owner and name:
        repository = GitHubRepository.objects.filter(owner=owner, name=name).first()

    headers = {key: value for key, value in request.headers.items()}
    query_params = {key: request.GET.getlist(key) for key in request.GET.keys()}

    event = GitHubEvent.objects.create(
        repository=repository,
        owner=owner or "",
        name=name or "",
        event_type=request.headers.get("X-GitHub-Event", ""),
        delivery_id=request.headers.get("X-GitHub-Delivery", ""),
        hook_id=request.headers.get("X-GitHub-Hook-ID", ""),
        signature=request.headers.get("X-Hub-Signature", ""),
        signature_256=request.headers.get("X-Hub-Signature-256", ""),
        user_agent=request.headers.get("User-Agent", ""),
        http_method=request.method,
        headers=headers,
        query_params=query_params,
        payload=payload,
        raw_body=raw_body,
    )

    return JsonResponse({"status": "ok", "event_id": event.pk})
