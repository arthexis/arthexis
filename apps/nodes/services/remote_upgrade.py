from __future__ import annotations

import os
import uuid
from collections.abc import Iterable
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.system.upgrade import UPGRADE_CHANNEL_CHOICES, _trigger_upgrade_check
from apps.core.versioning import normalize_upgrade_channel
from apps.features.utils import is_suite_feature_enabled
from apps.nodes.models import (
    NetMessage,
    Node,
    RemoteUpgradeRequest,
    _upgrade_in_progress,
)

REMOTE_UPGRADE_FEATURE_SLUG = "remote-upgrade-requests"
REMOTE_UPGRADE_ENV = "ARTHEXIS_REMOTE_UPGRADE_REQUESTS"
REMOTE_UPGRADE_ALLOWED_CHANNELS_ENV = "ARTHEXIS_REMOTE_UPGRADE_ALLOWED_CHANNELS"
REMOTE_UPGRADE_ALLOWED_UPSTREAMS_ENV = "ARTHEXIS_REMOTE_UPGRADE_ALLOWED_UPSTREAMS"
REMOTE_UPGRADE_ALLOWED_UPSTREAM_ROLES_ENV = "ARTHEXIS_REMOTE_UPGRADE_ALLOWED_UPSTREAM_ROLES"
REMOTE_UPGRADE_DEFAULT_ALLOWED_CHANNELS = ("stable", "regular")
REMOTE_UPGRADE_DEFAULT_ROLE_NAMES = {"satellite"}


def _env_flag(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str) -> set[str]:
    raw_value = os.environ.get(name, "")
    return {part.strip().lower() for part in raw_value.split(",") if part.strip()}


def _local_role_keys(local_node: Node | None) -> set[str]:
    keys: set[str] = set()
    role = getattr(local_node, "role", None) if local_node else None
    for value in (
        getattr(role, "name", ""),
        getattr(role, "acronym", ""),
        getattr(settings, "NODE_ROLE", ""),
        os.environ.get("NODE_ROLE", ""),
    ):
        if value:
            keys.add(str(value).strip().casefold())
    return keys


def remote_upgrade_acceptance_enabled(local_node: Node | None = None) -> bool:
    """Return whether this node accepts remote upgrade requests at all."""

    if not is_suite_feature_enabled(REMOTE_UPGRADE_FEATURE_SLUG, default=True):
        return False
    env_enabled = _env_flag(REMOTE_UPGRADE_ENV)
    if env_enabled is not None:
        return env_enabled
    return bool(REMOTE_UPGRADE_DEFAULT_ROLE_NAMES.intersection(_local_role_keys(local_node)))


def allowed_remote_upgrade_channels() -> set[str]:
    """Return canonical upgrade channels accepted from remote requests."""

    configured = _csv_env(REMOTE_UPGRADE_ALLOWED_CHANNELS_ENV)
    raw_channels: Iterable[str] = configured or REMOTE_UPGRADE_DEFAULT_ALLOWED_CHANNELS
    return {
        normalized
        for channel in raw_channels
        if (normalized := normalize_upgrade_channel(channel))
    }


def normalize_remote_upgrade_channel(channel: str) -> str:
    """Validate and canonicalize a requested upgrade channel."""

    channel_key = (channel or "").strip().lower()
    if channel_key not in UPGRADE_CHANNEL_CHOICES:
        available = ", ".join(sorted(UPGRADE_CHANNEL_CHOICES))
        raise ValidationError(f"Unsupported upgrade channel '{channel}'. Available: {available}.")
    normalized = normalize_upgrade_channel(channel_key)
    if not normalized:
        raise ValidationError(f"Unsupported upgrade channel '{channel}'.")
    return normalized


def resolve_remote_upgrade_target(value: str) -> Node:
    """Resolve a target node by primary identifiers accepted by the CLI."""

    raw_value = (value or "").strip()
    if not raw_value:
        raise ValidationError("A target node is required.")

    filters = (
        models.Q(hostname__iexact=raw_value)
        | models.Q(network_hostname__iexact=raw_value)
        | models.Q(public_endpoint__iexact=raw_value)
    )
    try:
        filters |= models.Q(pk=int(raw_value))
    except ValueError:
        pass
    try:
        filters |= models.Q(uuid=uuid.UUID(raw_value))
    except ValueError:
        pass

    node = Node.objects.filter(filters).order_by("pk").first()
    if node is None:
        raise ValidationError(f"Node not found: {raw_value}")
    return node


def _uuid_from_payload(value: object) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _dict_from_payload(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _validation_error_message(exc: ValidationError) -> str:
    messages = getattr(exc, "messages", None)
    if messages:
        return "; ".join(str(message) for message in messages)
    return str(exc)


def _node_matches_allowed_upstream(sender: Node) -> bool:
    allowed = _csv_env(REMOTE_UPGRADE_ALLOWED_UPSTREAMS_ENV)
    if not allowed:
        return True
    candidates = {
        str(sender.pk).lower(),
        str(sender.uuid).lower(),
        (sender.hostname or "").lower(),
        (sender.network_hostname or "").lower(),
        (sender.public_endpoint or "").lower(),
        (sender.mac_address or "").lower(),
    }
    return bool(allowed.intersection(candidates))


def _role_matches_allowed_upstream(sender: Node) -> bool:
    allowed = _csv_env(REMOTE_UPGRADE_ALLOWED_UPSTREAM_ROLES_ENV)
    if not allowed:
        return True
    role_name = getattr(getattr(sender, "role", None), "name", "")
    role_acronym = getattr(getattr(sender, "role", None), "acronym", "")
    candidates = {role_name.lower(), role_acronym.lower()}
    return bool(allowed.intersection(candidates))


def _set_rejected(request: RemoteUpgradeRequest, reason: str) -> RemoteUpgradeRequest:
    now = timezone.now()
    request.status = RemoteUpgradeRequest.Status.REJECTED
    request.rejection_reason = reason[:256]
    request.rejected_at = now
    request.responded_at = now
    request.save(
        update_fields=[
            "status",
            "rejection_reason",
            "rejected_at",
            "responded_at",
            "updated",
        ]
    )
    return request


def _send_remote_upgrade_response(
    request: RemoteUpgradeRequest,
    *,
    target: Node,
) -> None:
    """Send the downstream decision back to the requesting upstream node."""

    if not target.pk:
        return
    local = Node.get_local()
    message = NetMessage.objects.create(
        subject="Remote upgrade response",
        body=f"{request.channel}: {request.status}",
        kind=NetMessage.Kind.REMOTE_UPGRADE_RESPONSE,
        control_payload={"remote_upgrade_response": request.to_response_payload()},
        node_origin=local,
        filter_node=target,
        target_limit=1,
    )
    message.propagate(allow_remote_upgrade_control=True)


def create_remote_upgrade_request(
    *,
    target: Node,
    channel: str = "stable",
    reason: str = "",
    expires_in_minutes: int = 60,
) -> RemoteUpgradeRequest:
    """Create and send a signed remote upgrade request to a downstream node."""

    if target.current_relation != Node.Relation.DOWNSTREAM:
        raise ValidationError("Remote upgrade requests can only target downstream nodes.")

    normalized_channel = normalize_remote_upgrade_channel(channel)
    local = Node.get_local()
    if local is None:
        raise ValidationError("Local node is not registered.")

    expires_at = timezone.now() + timedelta(minutes=max(1, int(expires_in_minutes)))
    request = RemoteUpgradeRequest.objects.create(
        origin_node=local,
        target_node=target,
        origin_uuid=local.uuid,
        target_uuid=target.uuid,
        channel=normalized_channel,
        reason=(reason or "").strip()[:256],
        expires_at=expires_at,
    )
    message = NetMessage.objects.create(
        subject="Remote upgrade request",
        body=f"{normalized_channel}: {request.reason}"[:256],
        kind=NetMessage.Kind.REMOTE_UPGRADE_REQUEST,
        control_payload={"remote_upgrade_request": request.to_request_payload()},
        node_origin=local,
        filter_node=target,
        target_limit=1,
        expires_at=expires_at,
    )
    message.propagate(allow_remote_upgrade_control=True)
    return request


def receive_remote_upgrade_response(payload: dict[str, object], *, sender: Node) -> RemoteUpgradeRequest | None:
    """Apply a downstream response to the origin node's request record."""

    response_payload = payload.get("remote_upgrade_response")
    if not isinstance(response_payload, dict):
        return None
    request_uuid = _uuid_from_payload(response_payload.get("uuid"))
    if request_uuid is None:
        return None
    request = RemoteUpgradeRequest.objects.filter(uuid=request_uuid).first()
    if request is None:
        return None

    status = str(response_payload.get("status") or "").strip()
    valid_statuses = {choice.value for choice in RemoteUpgradeRequest.Status}
    if status in valid_statuses:
        request.status = status
    request.rejection_reason = str(response_payload.get("rejection_reason") or "")[:256]
    request.trigger_result = str(response_payload.get("trigger_result") or "")[:128]
    request.responded_at = timezone.now()
    if request.target_node_id is None:
        request.target_node = sender
    request.save(
        update_fields=[
            "status",
            "rejection_reason",
            "trigger_result",
            "responded_at",
            "target_node",
            "updated",
        ]
    )
    return request


def _terminal_statuses() -> set[str]:
    return {
        RemoteUpgradeRequest.Status.REJECTED,
        RemoteUpgradeRequest.Status.QUEUED,
        RemoteUpgradeRequest.Status.STARTED,
        RemoteUpgradeRequest.Status.COMPLETED,
    }


def _remote_upgrade_rejection_reason(
    *,
    request: RemoteUpgradeRequest,
    sender: Node,
    local: Node | None,
    target_uuid: uuid.UUID | None,
    channel: str,
) -> str | None:
    if local is None:
        return "Local node is not registered."
    if target_uuid is None or target_uuid != local.uuid:
        return "Request target does not match this node."
    if request.expires_at and request.expires_at <= timezone.now():
        return "Request expired."
    if sender.current_relation != Node.Relation.UPSTREAM:
        return "Sender is not registered as an upstream node."
    if not remote_upgrade_acceptance_enabled(local):
        return "Remote upgrade requests are disabled on this node."
    if not _node_matches_allowed_upstream(sender):
        return "Sender is not in the allowed upstream list."
    if not _role_matches_allowed_upstream(sender):
        return "Sender role is not allowed."
    if channel not in allowed_remote_upgrade_channels():
        return f"Channel '{channel}' is not allowed."
    if _upgrade_in_progress():
        return "An upgrade is already in progress."
    return None


def receive_remote_upgrade_request(payload: dict[str, object], *, sender: Node) -> RemoteUpgradeRequest | None:
    """Receive, audit, and possibly accept a downstream upgrade request."""

    request_payload = payload.get("remote_upgrade_request")
    if not isinstance(request_payload, dict):
        return None

    request_uuid = _uuid_from_payload(request_payload.get("uuid"))
    target_uuid = _uuid_from_payload(request_payload.get("target_uuid"))
    origin_uuid = _uuid_from_payload(request_payload.get("origin_uuid")) or sender.uuid
    if request_uuid is None:
        return None

    local = Node.get_local()
    raw_channel = str(request_payload.get("channel") or "stable")
    try:
        channel = normalize_remote_upgrade_channel(raw_channel)
    except ValidationError as exc:
        defaults = {
            "origin_node": sender,
            "target_node": local,
            "origin_uuid": origin_uuid,
            "target_uuid": target_uuid,
            "channel": raw_channel[:20],
            "options": _dict_from_payload(request_payload.get("options")),
            "reason": str(request_payload.get("reason") or "")[:256],
            "expires_at": NetMessage.normalize_expires_at(request_payload.get("expires_at")),
            "status": RemoteUpgradeRequest.Status.RECEIVED,
        }
        request, created = RemoteUpgradeRequest.objects.get_or_create(
            uuid=request_uuid,
            defaults=defaults,
        )
        if request.status in _terminal_statuses():
            _send_remote_upgrade_response(request, target=sender)
            return request
        if not created:
            for field, value in defaults.items():
                setattr(request, field, value)
            request.save(update_fields=[*defaults.keys(), "updated"])
        rejected = _set_rejected(request, _validation_error_message(exc))
        _send_remote_upgrade_response(rejected, target=sender)
        return rejected

    defaults = {
        "origin_node": sender,
        "target_node": local,
        "origin_uuid": origin_uuid,
        "target_uuid": target_uuid,
        "channel": channel,
        "options": _dict_from_payload(request_payload.get("options")),
        "reason": str(request_payload.get("reason") or "")[:256],
        "expires_at": NetMessage.normalize_expires_at(request_payload.get("expires_at")),
        "status": RemoteUpgradeRequest.Status.RECEIVED,
    }
    request, created = RemoteUpgradeRequest.objects.get_or_create(
        uuid=request_uuid,
        defaults=defaults,
    )
    if not created:
        if request.status in _terminal_statuses():
            _send_remote_upgrade_response(request, target=sender)
            return request
        for field, value in defaults.items():
            setattr(request, field, value)
        request.save(update_fields=[*defaults.keys(), "updated"])

    def reject(reason: str) -> RemoteUpgradeRequest:
        rejected = _set_rejected(request, reason)
        _send_remote_upgrade_response(rejected, target=sender)
        return rejected

    rejection_reason = _remote_upgrade_rejection_reason(
        request=request,
        sender=sender,
        local=local,
        target_uuid=target_uuid,
        channel=channel,
    )
    if rejection_reason:
        return reject(rejection_reason)

    queued = _trigger_upgrade_check(
        channel_override=None if channel == "stable" else channel
    )
    now = timezone.now()
    request.status = (
        RemoteUpgradeRequest.Status.QUEUED
        if queued
        else RemoteUpgradeRequest.Status.STARTED
    )
    request.accepted_at = now
    request.queued_at = now
    request.responded_at = now
    request.rejection_reason = ""
    request.trigger_result = "queued" if queued else "started locally"
    request.save(
        update_fields=[
            "status",
            "accepted_at",
            "queued_at",
            "responded_at",
            "rejection_reason",
            "trigger_result",
            "updated",
        ]
    )
    _send_remote_upgrade_response(request, target=sender)
    return request
