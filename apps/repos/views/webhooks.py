"""Webhook endpoints for repository integrations."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qs
from typing import TypeAlias, TypedDict

from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.repos.models.events import GitHubEvent
from apps.repos.models.github_apps import GitHubApp
from apps.repos.models.repositories import GitHubRepository
from apps.repos.spam_filter import assess_github_issue_event

logger = logging.getLogger(__name__)


class GitHubWebhookOwnerPayload(TypedDict, total=False):
    """Subset of GitHub owner payload fields used by webhook routing."""

    login: str


class GitHubWebhookRepositoryPayload(TypedDict, total=False):
    """Subset of repository payload fields used by webhook routing."""

    full_name: str
    name: str
    owner: GitHubWebhookOwnerPayload


class GitHubWebhookPayload(TypedDict, total=False):
    """Webhook payload fields consumed by Arthexis webhook ingestion."""

    items: list[object]
    repository: GitHubWebhookRepositoryPayload


ParsedWebhookPayload: TypeAlias = dict[str, object]
RepositoryRoute: TypeAlias = tuple[str, str]


def _load_payload_object(raw_payload: str) -> ParsedWebhookPayload | None:
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"items": parsed}
    return None


def _extract_payload(request: HttpRequest) -> tuple[ParsedWebhookPayload, str]:
    raw_bytes = request.body or b""
    raw_body = raw_bytes.decode("utf-8", errors="replace")

    payload_field = request.POST.get("payload")
    content_type = request.content_type or ""
    if payload_field is None and raw_body and content_type.startswith(
        "application/x-www-form-urlencoded"
    ):
        payload_values = parse_qs(raw_body, keep_blank_values=True).get("payload")
        if payload_values:
            payload_field = payload_values[0]
    if payload_field:
        parsed_payload = _load_payload_object(payload_field)
        if parsed_payload is not None:
            return parsed_payload, raw_body

    if not raw_body:
        return {}, raw_body

    parsed_payload = _load_payload_object(raw_body)
    return parsed_payload or {}, raw_body


def _payload_for_routing(payload: ParsedWebhookPayload) -> GitHubWebhookPayload:
    routed_payload: GitHubWebhookPayload = {}

    repository = payload.get("repository")
    if isinstance(repository, dict):
        owner = repository.get("owner")

        owner_payload: GitHubWebhookOwnerPayload = {}
        if isinstance(owner, dict):
            login = owner.get("login")
            if isinstance(login, str):
                owner_payload["login"] = login

        repository_payload: GitHubWebhookRepositoryPayload = {}
        name = repository.get("name")
        full_name = repository.get("full_name")

        if isinstance(name, str):
            repository_payload["name"] = name
        if isinstance(full_name, str):
            repository_payload["full_name"] = full_name
        if owner_payload:
            repository_payload["owner"] = owner_payload
        if repository_payload:
            routed_payload["repository"] = repository_payload

    items = payload.get("items")
    if isinstance(items, list):
        routed_payload["items"] = items

    return routed_payload


def _extract_repository(payload: GitHubWebhookPayload) -> RepositoryRoute:
    repository = payload.get("repository")
    if not repository:
        return "", ""

    owner_info = repository.get("owner")
    owner_name = owner_info.get("login", "") if owner_info else ""
    name = repository.get("name", "")

    if not owner_name:
        full_name = repository.get("full_name", "")
        if "/" in full_name:
            owner_name, split_name = full_name.split("/", 1)
            name = name or split_name

    return owner_name, name


def _resolve_event_route(
    owner: str,
    name: str,
    payload: GitHubWebhookPayload,
) -> RepositoryRoute:
    payload_owner, payload_name = _extract_repository(payload)
    return owner or payload_owner, name or payload_name


def _verify_signature(request: HttpRequest, secret: str) -> bool:
    if not secret:
        return False

    raw_bytes = request.body or b""
    signature_256 = request.headers.get("X-Hub-Signature-256", "")
    if signature_256:
        expected = "sha256=" + hmac.new(
            secret.encode("utf-8"),
            raw_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature_256, expected)

    signature = request.headers.get("X-Hub-Signature", "")
    if signature:
        expected = "sha1=" + hmac.new(
            secret.encode("utf-8"),
            raw_bytes,
            hashlib.sha1,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    return False


@csrf_exempt
def github_webhook(
    request: HttpRequest,
    owner: str = "",
    name: str = "",
    app_slug: str = "",
) -> JsonResponse:
    if app_slug:
        app = GitHubApp.objects.filter(
            Q(webhook_slug=app_slug) | Q(app_slug=app_slug)
        ).first()
        if not app or not _verify_signature(request, app.webhook_secret):
            return JsonResponse({"status": "unauthorized"}, status=401)

    payload, raw_body = _extract_payload(request)
    owner, name = _resolve_event_route(owner, name, _payload_for_routing(payload))

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
        http_method=request.method or "",
        headers=headers,
        query_params=query_params,
        payload=payload,
        raw_body=raw_body,
    )
    try:
        assess_github_issue_event(event)
    except Exception:  # pragma: no cover - defensive guard to keep webhook ingestion resilient
        logger.exception("GitHub issue spam assessment failed for event_id=%s", event.pk)

    return JsonResponse({"status": "ok", "event_id": event.pk})
