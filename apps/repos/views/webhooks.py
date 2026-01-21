"""Webhook endpoints for repository integrations."""

from __future__ import annotations

import json
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.repos.models.events import GitHubEvent
from apps.repos.models.repositories import GitHubRepository


def _extract_payload(request: HttpRequest) -> tuple[dict[str, Any], str]:
    raw_bytes = request.body or b""
    raw_body = raw_bytes.decode("utf-8", errors="replace")
    payload: dict[str, Any] = {}

    if not raw_bytes:
        return payload, raw_body

    if request.content_type == "application/x-www-form-urlencoded":
        payload_field = request.POST.get("payload")
        if payload_field:
            try:
                parsed = json.loads(payload_field)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed, raw_body

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
        owner = repository.get("owner") or {}
        if isinstance(owner, dict):
            owner_name = owner.get("login") or ""
        else:
            owner_name = ""
        name = repository.get("name") or ""
        if not owner_name:
            full_name = repository.get("full_name") or ""
            if "/" in full_name:
                owner_name, name = full_name.split("/", 1)
        return str(owner_name), str(name)
    return "", ""


@csrf_exempt
def github_webhook(request: HttpRequest, owner: str = "", name: str = "") -> JsonResponse:
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
        event_type=headers.get("X-GitHub-Event", ""),
        delivery_id=headers.get("X-GitHub-Delivery", ""),
        hook_id=headers.get("X-GitHub-Hook-Id", "")
        or headers.get("X-GitHub-Hook-ID", ""),
        signature=headers.get("X-Hub-Signature", ""),
        signature_256=headers.get("X-Hub-Signature-256", ""),
        user_agent=headers.get("User-Agent", ""),
        http_method=request.method,
        headers=headers,
        query_params=query_params,
        payload=payload,
        raw_body=raw_body,
    )

    return JsonResponse({"status": "ok", "event_id": event.pk})
