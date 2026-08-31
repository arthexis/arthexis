from __future__ import annotations

import hmac
import os
import re
import shutil
import socket
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

import requests
from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.nodes.models import Node
from apps.repos.github_monitor import local_node_role
from apps.repos.github_monitor import patchwork_dir as resolve_patchwork_dir
from apps.repos.models import (
    GitHubRepository,
    RepositoryIssue,
    RepositoryPullRequest,
    RepositoryWorkAssignment,
    RepositoryWorkNodeSnapshot,
)
from apps.repos.pr_oversee.affinity import infer_work_profile

ASSIGNMENT_SYNC_PATH = "/repos/work/assignments/sync/"
ASSIGNMENT_SYNC_HEADER = "X-Arthexis-Assignment-Token"
REQUEST_TIMEOUT_SECONDS = 10
CONTROL_FIT_HARDWARE = {
    "camera",
    "display",
    "gpio",
    "raspberry-pi",
    "rfid",
    "usb",
}
CONTROL_ROLE_FIT_TERMS = {
    "charger",
    "control-node",
    "debian",
    "gway",
    "ocpp",
    "ocpp16",
    "ocpp201",
    "ocpp21",
    "ocpp-gateway",
    "raspberry",
    "raspberry-pi",
    "realtek",
    "rpi",
    "rpi-debian",
}
CONTROL_PAIRED_ROLE_FIT_TERMS = {
    "charger",
    "ocpp",
}
CONTROL_PLATFORM_ROLE_FIT_TERMS = {
    "gway",
    "raspberry",
    "raspberry-pi",
    "rpi",
    "rpi-debian",
}
CONTROL_CAPABILITY_FIT_TERMS = {
    "camera",
    "gpio",
    "imager",
    "lcd",
    "llm-summary",
    "rfid",
    "serial",
    "summary",
    "usb",
}
CONTROL_FIT_TERMS = CONTROL_ROLE_FIT_TERMS | CONTROL_CAPABILITY_FIT_TERMS
CONTROL_MANUAL_PATCHWORK_REASON_MARKER = "operator-authorized-control-patchwork"
NODE_FEATURE_CAPABILITY_ALIASES = {
    "gpio-rtc": ("gpio",),
    "lcd-screen": ("display", "lcd"),
    "llm-summary": ("summary",),
    "rfid-scanner": ("rfid",),
    "usb-inventory": ("usb",),
}


class AssignmentSyncError(RuntimeError):
    """Raised when repository work assignments cannot be synced."""


def assignment_sync_url(upstream_url: str) -> str:
    """Return the assignment sync endpoint for a configured upstream URL."""

    cleaned = str(upstream_url or "").strip()
    if not cleaned:
        return ""
    if cleaned.rstrip("/").endswith(ASSIGNMENT_SYNC_PATH.rstrip("/")):
        return f"{cleaned.rstrip('/')}/"
    return f"{cleaned.rstrip('/')}{ASSIGNMENT_SYNC_PATH}"


def assignment_sync_configured() -> bool:
    """Return whether this node has enough config to pull upstream assignments."""

    return bool(configured_upstream_url() and configured_sync_token())


def configured_upstream_url() -> str:
    """Return the configured upstream assignment URL, including legacy aliases."""

    return (
        str(getattr(settings, "REPOSITORY_ASSIGNMENT_UPSTREAM_URL", "") or "").strip()
        or str(
            getattr(settings, "REPOSITORY_WORK_ASSIGNMENT_UPSTREAM_URL", "") or ""
        ).strip()
    )


def configured_sync_token() -> str:
    """Return the configured assignment sync token, including legacy aliases."""

    return (
        str(getattr(settings, "REPOSITORY_ASSIGNMENT_SYNC_TOKEN", "") or "").strip()
        or str(
            getattr(settings, "REPOSITORY_WORK_ASSIGNMENT_SYNC_TOKEN", "") or ""
        ).strip()
    )


def configured_timeout() -> int:
    """Return the configured upstream assignment request timeout."""

    raw_value = (
        getattr(settings, "REPOSITORY_ASSIGNMENT_SYNC_TIMEOUT_SECONDS", None)
        or getattr(settings, "REPOSITORY_WORK_ASSIGNMENT_SYNC_TIMEOUT_SECONDS", None)
        or REQUEST_TIMEOUT_SECONDS
    )
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return REQUEST_TIMEOUT_SECONDS


def assignment_sync_token_authorized(header_value: object) -> bool:
    """Return whether a request header carries the configured sync token."""

    expected = configured_sync_token()
    if not expected:
        return False
    supplied = str(header_value or "").strip()
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    try:
        supplied_bytes = supplied.encode("ascii")
        expected_bytes = expected.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(supplied_bytes, expected_bytes)


def _json_object(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text_values(value: object) -> list[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, Mapping):
        for key in ("slug", "name", "display", "label", "title"):
            cleaned = str(value.get(key) or "").strip()
            if cleaned:
                return [cleaned]
        return []
    if isinstance(value, Iterable):
        values: list[str] = []
        for item in value:
            values.extend(_text_values(item))
        return values
    return []


def _normalized_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _normalized_text_terms(values: Iterable[str]) -> set[str]:
    text = " ".join(str(value or "") for value in values)
    normalized_text = text.casefold()
    tokens = re.findall(r"[a-z0-9]+", normalized_text)
    terms = set(tokens)
    for size in range(2, 4):
        terms.update(
            "-".join(tokens[index : index + size])
            for index in range(0, max(len(tokens) - size + 1, 0))
        )
    for match in re.finditer(
        r"\bocpp\s*([0-9]+)(?:\s*[.-]\s*([0-9]+))?(?:\s*[.-]\s*([0-9]+))?",
        normalized_text,
    ):
        version_parts = [part for part in match.groups() if part is not None]
        if version_parts:
            terms.add(f"ocpp{''.join(version_parts)}")
    return terms


def _token_matches(term: str, tokens: set[str]) -> bool:
    return any(term == token or term in token or token in term for token in tokens)


def _parse_timestamp(value: object) -> timezone.datetime:
    if isinstance(value, timezone.datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = parse_datetime(value.strip()) or timezone.now()
        except ValueError:
            parsed = timezone.now()
    else:
        parsed = timezone.now()
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=timezone.get_current_timezone())
    return parsed


def _slugify_endpoint(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or f"repo-node-{uuid.uuid4().hex[:8]}"


def _unique_public_endpoint(seed: str) -> str:
    base = _slugify_endpoint(seed)
    candidate = base
    suffix = 1
    while Node.objects.filter(public_endpoint=candidate).exists():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def _parse_node_uuid(value: object) -> uuid.UUID | None:
    uuid_value = str(value or "").strip()
    if uuid_value:
        try:
            return uuid.UUID(uuid_value)
        except ValueError:
            return None
    return None


def _find_reported_node(
    *,
    node_uuid: uuid.UUID | None,
    public_endpoint: str,
    hostname: str,
) -> Node | None:
    if node_uuid is not None:
        node = Node.objects.filter(uuid=node_uuid).first()
        if node is not None:
            return node
        return None
    if public_endpoint:
        node = Node.objects.filter(public_endpoint=public_endpoint).first()
        if node is not None:
            return node
        return None
    if hostname:
        node = Node.objects.filter(hostname__iexact=hostname).order_by("pk").first()
        if node is not None:
            return node
    return None


def _create_reported_node(
    *,
    node_uuid: uuid.UUID | None,
    public_endpoint: str,
    hostname: str,
) -> Node:
    fallback_name = hostname or "downstream-node"
    return Node.objects.create(
        hostname=hostname or public_endpoint or "downstream-node",
        public_endpoint=public_endpoint or _unique_public_endpoint(fallback_name),
        uuid=node_uuid or uuid.uuid4(),
        current_relation=Node.Relation.DOWNSTREAM,
    )


def _sync_reported_node_fields(
    node: Node,
    *,
    public_endpoint: str,
    hostname: str,
) -> None:
    if node.current_relation != Node.Relation.DOWNSTREAM:
        raise AssignmentSyncError("node is not eligible for assignment sync")
    if hostname and node.hostname.casefold() != hostname.casefold():
        raise AssignmentSyncError("conflicting node identity")
    if public_endpoint and node.public_endpoint != public_endpoint:
        raise AssignmentSyncError("conflicting node identity")


def _resolve_reported_node(node_payload: Mapping[str, Any]) -> Node:
    hostname = str(node_payload.get("hostname") or "").strip()
    public_endpoint = str(node_payload.get("public_endpoint") or "").strip()
    raw_uuid = str(node_payload.get("uuid") or "").strip()
    node_uuid = _parse_node_uuid(raw_uuid)
    if raw_uuid and node_uuid is None:
        raise AssignmentSyncError("invalid node identity")
    if node_uuid is None and not public_endpoint and not hostname:
        raise AssignmentSyncError("node identity is required")
    if node_uuid is not None and public_endpoint:
        endpoint_node = Node.objects.filter(public_endpoint=public_endpoint).first()
        if endpoint_node is not None and endpoint_node.uuid != node_uuid:
            raise AssignmentSyncError("conflicting node identity")

    node = _find_reported_node(
        node_uuid=node_uuid,
        public_endpoint=public_endpoint,
        hostname=hostname,
    )
    if node is None:
        return _create_reported_node(
            node_uuid=node_uuid,
            public_endpoint=public_endpoint,
            hostname=hostname,
        )

    _sync_reported_node_fields(
        node,
        public_endpoint=public_endpoint,
        hostname=hostname,
    )
    return node


def record_downstream_snapshot(
    payload: Mapping[str, Any], *, upstream_url: str = ""
) -> tuple[Node, RepositoryWorkNodeSnapshot]:
    """Store the downstream node metadata posted to an upstream node."""

    node = _resolve_reported_node(_json_object(payload.get("node")))
    reported_at = _parse_timestamp(payload.get("reported_at"))
    snapshot, _created = RepositoryWorkNodeSnapshot.objects.update_or_create(
        node=node,
        defaults={
            "capabilities": _json_object(payload.get("capabilities")),
            "current_load": _json_object(payload.get("current_load")),
            "developer_info": _json_object(payload.get("developer_info")),
            "reported_at": reported_at,
            "upstream_url": upstream_url,
        },
    )
    return node, snapshot


def _repository_from_slug(
    slug: object,
    *,
    create: bool = True,
) -> GitHubRepository | None:
    cleaned = str(slug or "").strip()
    if "/" not in cleaned:
        return None
    owner, name = [segment.strip() for segment in cleaned.split("/", 1)]
    if not owner or not name:
        return None
    if not create:
        return GitHubRepository.objects.filter(owner=owner, name=name).first()
    repository, _created = GitHubRepository.objects.get_or_create(
        owner=owner, name=name
    )
    return repository


def _target_type(value: object) -> str | None:
    cleaned = str(value or "").strip().lower()
    if cleaned in {"issue", "issues"}:
        return RepositoryWorkAssignment.TargetType.ISSUE
    if cleaned in {"pr", "pull_request", "pull-request", "pull request"}:
        return RepositoryWorkAssignment.TargetType.PULL_REQUEST
    return None


def _work_item_for_assignment(
    assignment: RepositoryWorkAssignment,
) -> RepositoryIssue | RepositoryPullRequest | None:
    model = (
        RepositoryPullRequest
        if assignment.target_type == RepositoryWorkAssignment.TargetType.PULL_REQUEST
        else RepositoryIssue
    )
    return model.objects.filter(
        repository=assignment.repository,
        number=assignment.number,
    ).first()


def _node_snapshot_capabilities(node: Node) -> dict[str, Any] | None:
    try:
        snapshot = node.repository_work_snapshot
    except RepositoryWorkNodeSnapshot.DoesNotExist:
        return None
    return _json_object(snapshot.capabilities)


def _node_role_for_assignment_fit(node: Node, capabilities: Mapping[str, Any]) -> str:
    role = str(capabilities.get("node_role") or "").strip()
    if role:
        return role
    stored_role = str(getattr(getattr(node, "role", None), "name", "") or "").strip()
    if stored_role:
        return stored_role
    return ""


def _assignment_work_labels(
    item: RepositoryIssue | RepositoryPullRequest | None,
) -> list[str]:
    return _text_values(getattr(item, "labels", [])) if item is not None else []


def control_manual_patchwork_reason(base_reason: str = "") -> str:
    """Return an assignment reason that preserves explicit Control patchwork auth."""

    cleaned = str(base_reason or "").strip()
    marker = f"[{CONTROL_MANUAL_PATCHWORK_REASON_MARKER}]"
    if marker in cleaned:
        return cleaned
    return f"{cleaned} {marker}".strip()


def _control_manual_patchwork_authorized(assignment: RepositoryWorkAssignment) -> bool:
    reason = str(assignment.reason or "").casefold()
    return CONTROL_MANUAL_PATCHWORK_REASON_MARKER in reason


def _profile_reason_for_assignment(assignment: RepositoryWorkAssignment) -> str:
    reason = str(assignment.reason or "")
    marker = rf"\[{re.escape(CONTROL_MANUAL_PATCHWORK_REASON_MARKER)}\]"
    return re.sub(marker, " ", reason, flags=re.IGNORECASE).strip()


def _assignment_node_fit(
    assignment: RepositoryWorkAssignment,
    *,
    node: Node,
    item: RepositoryIssue | RepositoryPullRequest | None = None,
    capabilities: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if capabilities is None:
        capabilities = _node_snapshot_capabilities(node)
    else:
        capabilities = _json_object(capabilities)
    node_role = _node_role_for_assignment_fit(node, capabilities or {})
    if assignment.status == RepositoryWorkAssignment.Status.REMOVED:
        return {
            "eligible": False,
            "classification": "removed",
            "nodeRole": node_role,
            "reasons": ["assignment-removed"],
        }
    if not capabilities:
        return {
            "eligible": True,
            "classification": "capabilities-not-evaluated",
            "nodeRole": node_role,
            "reasons": ["capabilities-unavailable"],
        }
    if item is None:
        item = _work_item_for_assignment(assignment)
    if item is None and node_role.casefold() == "control":
        return {
            "eligible": True,
            "classification": "target-metadata-unavailable",
            "nodeRole": node_role,
            "reasons": ["target-metadata-unavailable"],
        }
    title = str(getattr(item, "title", "") or "")
    labels = _assignment_work_labels(item)
    node_features = _text_values(capabilities.get("node_features"))
    suite_features = _text_values(capabilities.get("suite_features"))
    capability_terms = _text_values(capabilities.get("capability_terms"))
    profile_reason = _profile_reason_for_assignment(assignment)
    body = " ".join([profile_reason, *labels])
    profile = infer_work_profile(title=title, body=body, files=())
    reasons = list(profile.get("reasons") or [])

    if node_role.casefold() != "control":
        return {
            "eligible": True,
            "classification": "role-not-restricted",
            "nodeRole": node_role,
            "reasons": reasons or ["non-control-node"],
        }

    work_tokens = _normalized_text_terms([title, profile_reason, *labels])
    capability_tokens = {
        _normalized_token(value)
        for value in [
            *_capability_terms_from_node_features(node_features),
            *capability_terms,
        ]
        if str(value).strip()
    }
    matched_terms = sorted(term for term in CONTROL_FIT_TERMS if term in work_tokens)
    matched_role_terms = sorted(set(matched_terms) & CONTROL_ROLE_FIT_TERMS)
    matched_paired_role_terms = sorted(
        set(matched_role_terms) & CONTROL_PAIRED_ROLE_FIT_TERMS
    )
    matched_standalone_role_terms = sorted(
        set(matched_role_terms) - CONTROL_PAIRED_ROLE_FIT_TERMS
    )
    matched_capabilities = sorted(
        {
            term
            for term in set(matched_terms) & CONTROL_CAPABILITY_FIT_TERMS
            if _token_matches(term, capability_tokens)
        }
        | (work_tokens & capability_tokens)
    )
    profile_hardware = (
        set(str(item) for item in profile.get("hardware") or []) & CONTROL_FIT_HARDWARE
    )
    matched_hardware = sorted(
        tag for tag in profile_hardware if _token_matches(tag, capability_tokens)
    )
    matched_roles = [
        role for role in profile.get("roles") or [] if str(role).casefold() == "control"
    ]
    if (
        assignment.target_type == RepositoryWorkAssignment.TargetType.PULL_REQUEST
        and not matched_terms
        and not matched_roles
        and not profile_hardware
        and not profile.get("apps")
    ):
        return {
            "eligible": True,
            "classification": "pr-metadata-not-evaluated",
            "nodeRole": node_role,
            "matchedRoles": matched_roles,
            "matchedTerms": matched_terms,
            "matchedRoleTerms": matched_role_terms,
            "matchedCapabilities": matched_capabilities,
            "matchedHardware": matched_hardware,
            "reasons": sorted(dict.fromkeys([*reasons, "pr-paths-unavailable"])),
        }
    paired_role_fit = bool(
        matched_paired_role_terms
        and (matched_roles or matched_capabilities or matched_hardware)
    )
    platform_role_fit = bool(
        profile_hardware <= {"raspberry-pi"}
        and (set(matched_standalone_role_terms) & CONTROL_PLATFORM_ROLE_FIT_TERMS)
    )
    standalone_role_fit = bool(
        matched_standalone_role_terms
        and (
            not profile_hardware
            or platform_role_fit
            or matched_capabilities
            or matched_hardware
        )
    )
    if (
        standalone_role_fit
        or paired_role_fit
        or matched_capabilities
        or matched_hardware
    ):
        fit_reasons = [*reasons]
        if matched_roles and matched_role_terms:
            fit_reasons.append("control-fit-role")
        if matched_role_terms:
            fit_reasons.append("control-fit-role-terms")
        if matched_capabilities:
            fit_reasons.append("control-fit-capabilities")
        if matched_hardware:
            fit_reasons.append("control-fit-hardware")
        return {
            "eligible": True,
            "classification": "control-fit",
            "nodeRole": node_role,
            "matchedRoles": matched_roles,
            "matchedTerms": matched_terms,
            "matchedRoleTerms": matched_role_terms,
            "matchedCapabilities": matched_capabilities,
            "matchedHardware": matched_hardware,
            "reasons": sorted(dict.fromkeys(fit_reasons)),
        }

    affected_roles = [str(role) for role in profile.get("roles") or []]
    mismatch_reasons = [*reasons]
    mismatch_reasons.append("control-role-requires-hardware-or-rpi-fit")
    return {
        "eligible": False,
        "classification": "role-mismatch" if affected_roles else "generic-mismatch",
        "nodeRole": node_role,
        "affectedRoles": affected_roles,
        "matchedTerms": matched_terms,
        "matchedRoleTerms": matched_role_terms,
        "matchedCapabilities": matched_capabilities,
        "matchedHardware": matched_hardware,
        "reasons": sorted(dict.fromkeys(mismatch_reasons)),
    }


def serialize_assignment(
    assignment: RepositoryWorkAssignment,
    *,
    node: Node | None = None,
    item: RepositoryIssue | RepositoryPullRequest | None = None,
    capabilities: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a transport-safe assignment record."""

    if item is None:
        item = _work_item_for_assignment(assignment)
    node_fit = (
        _assignment_node_fit(
            assignment,
            node=node,
            item=item,
            capabilities=capabilities,
        )
        if node is not None
        else {"eligible": True, "classification": "not-evaluated", "reasons": []}
    )
    status = assignment.status
    patchwork_authorized = assignment.patchwork_authorized
    if status != RepositoryWorkAssignment.Status.REMOVED and not node_fit["eligible"]:
        status = RepositoryWorkAssignment.Status.REMOVED
        patchwork_authorized = False
    elif (
        status != RepositoryWorkAssignment.Status.REMOVED
        and patchwork_authorized
        and str(node_fit.get("nodeRole") or "").casefold() == "control"
        and not _control_manual_patchwork_authorized(assignment)
    ):
        patchwork_authorized = False
        node_fit = {
            **node_fit,
            "patchworkAuthorization": "manual-control-required",
            "reasons": sorted(
                dict.fromkeys(
                    [
                        *(str(reason) for reason in node_fit.get("reasons") or []),
                        "control-patchwork-requires-operator-authorization",
                    ]
                )
            ),
        }
    target_type = (
        "pr"
        if assignment.target_type == RepositoryWorkAssignment.TargetType.PULL_REQUEST
        else "issue"
    )
    payload = {
        "repo": assignment.repository.slug,
        "target_type": target_type,
        "number": assignment.number,
        "title": getattr(item, "title", "") if item is not None else "",
        "url": getattr(item, "html_url", "") if item is not None else "",
        "state": getattr(item, "state", "") if item is not None else "",
        "patchwork_authorized": patchwork_authorized,
        "status": status,
        "reason": assignment.reason,
        "node_fit": node_fit,
        "assigned_at": assignment.assigned_at.isoformat(),
        "updated_at": assignment.updated_at.isoformat(),
    }
    if item is not None:
        payload["labels"] = _assignment_work_labels(item)
    return payload


def _assignment_items_by_key(
    assignments: list[RepositoryWorkAssignment],
) -> dict[
    tuple[int, int, str],
    RepositoryIssue | RepositoryPullRequest,
]:
    repository_ids = {assignment.repository_id for assignment in assignments}
    issue_numbers = [
        assignment.number
        for assignment in assignments
        if assignment.target_type == RepositoryWorkAssignment.TargetType.ISSUE
    ]
    pull_request_numbers = [
        assignment.number
        for assignment in assignments
        if assignment.target_type == RepositoryWorkAssignment.TargetType.PULL_REQUEST
    ]
    items: dict[tuple[int, int, str], RepositoryIssue | RepositoryPullRequest] = {}
    if issue_numbers:
        for issue in RepositoryIssue.objects.filter(
            repository_id__in=repository_ids,
            number__in=issue_numbers,
        ):
            items[
                (
                    issue.repository_id,
                    issue.number,
                    RepositoryWorkAssignment.TargetType.ISSUE,
                )
            ] = issue
    if pull_request_numbers:
        for pull_request in RepositoryPullRequest.objects.filter(
            repository_id__in=repository_ids,
            number__in=pull_request_numbers,
        ):
            items[
                (
                    pull_request.repository_id,
                    pull_request.number,
                    RepositoryWorkAssignment.TargetType.PULL_REQUEST,
                )
            ] = pull_request
    return items


def assignments_for_node(
    node: Node,
    *,
    capabilities: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return repository assignments and removal tombstones targeted at ``node``."""

    assignments = RepositoryWorkAssignment.objects.filter(
        node=node,
        status__in=(
            RepositoryWorkAssignment.Status.ASSIGNED,
            RepositoryWorkAssignment.Status.ACTIVE,
            RepositoryWorkAssignment.Status.REMOVED,
        ),
    ).select_related("repository", "node")
    assignment_list = list(assignments)
    items = _assignment_items_by_key(assignment_list)
    return [
        serialize_assignment(
            assignment,
            node=node,
            item=items.get(
                (
                    assignment.repository_id,
                    assignment.number,
                    assignment.target_type,
                )
            ),
            capabilities=capabilities,
        )
        for assignment in assignment_list
    ]


def upstream_sync_response(
    payload: Mapping[str, Any], *, upstream_url: str = ""
) -> dict[str, Any]:
    """Record downstream info and return assignments targeted at that node."""

    node, snapshot = record_downstream_snapshot(payload, upstream_url=upstream_url)
    return {
        "schema_version": 1,
        "node": {
            "hostname": node.hostname,
            "public_endpoint": node.public_endpoint,
            "uuid": str(node.uuid),
        },
        "snapshot_reported_at": snapshot.reported_at.isoformat(),
        "assignments": assignments_for_node(node),
    }


def _assignment_dates(
    record: Mapping[str, Any],
) -> tuple[timezone.datetime, timezone.datetime]:
    updated_at = _parse_timestamp(record.get("updated_at"))
    assigned_at = _parse_timestamp(record.get("assigned_at") or updated_at)
    return assigned_at, updated_at


def _patchwork_authorized(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"", "0", "false", "no", "off"}:
            return False
    return False


def _assignment_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdecimal():
            number = int(cleaned)
            return number if number >= 1 else None
    return None


def _assignment_record_context(
    record: Mapping[str, Any],
) -> (
    tuple[
        GitHubRepository,
        int,
        str,
        timezone.datetime,
        timezone.datetime,
        str,
    ]
    | None
):
    number = _assignment_number(record.get("number"))
    if number is None:
        return None
    status = (
        str(record.get("status") or RepositoryWorkAssignment.Status.ASSIGNED)
        .strip()
        .lower()
    )
    allowed_statuses = {
        RepositoryWorkAssignment.Status.ASSIGNED,
        RepositoryWorkAssignment.Status.ACTIVE,
        RepositoryWorkAssignment.Status.REMOVED,
    }
    if status not in allowed_statuses:
        return None
    target_type = _target_type(record.get("target_type"))
    if target_type is None:
        return None
    repository = _repository_from_slug(
        record.get("repo"),
        create=status != RepositoryWorkAssignment.Status.REMOVED,
    )
    if repository is None:
        return None
    assigned_at, updated_at = _assignment_dates(record)
    return (
        repository,
        number,
        target_type,
        assigned_at,
        updated_at,
        status,
    )


def _work_item_defaults(
    record: Mapping[str, Any],
    *,
    number: int,
    updated_at: timezone.datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    title = str(record.get("title") or "").strip()
    state = str(record.get("state") or "").strip()
    html_url = str(record.get("url") or "").strip()
    labels = _text_values(record.get("labels"))
    update_defaults = {}
    if title:
        update_defaults["title"] = title
    if state:
        update_defaults["state"] = state
    if html_url:
        update_defaults["html_url"] = html_url
    if "labels" in record:
        update_defaults["labels"] = labels
    create_defaults = {
        "title": title or f"#{number}",
        "state": state or "open",
        "html_url": html_url,
        "api_url": "",
        "author": "",
        "labels": labels,
        "created_at": updated_at,
        "updated_at": updated_at,
    }
    return update_defaults, create_defaults


def _update_existing_issue_fields(
    issue: RepositoryIssue,
    *,
    update_defaults: dict[str, object],
) -> None:
    if update_defaults:
        RepositoryIssue.objects.filter(pk=issue.pk).update(**update_defaults)


def _update_existing_pull_request_fields(
    pull_request: RepositoryPullRequest,
    *,
    update_defaults: dict[str, object],
) -> None:
    if update_defaults:
        RepositoryPullRequest.objects.filter(pk=pull_request.pk).update(
            **update_defaults
        )


def _upsert_work_item(
    *,
    repository: GitHubRepository,
    target_type: str,
    number: int,
    record: Mapping[str, Any],
    updated_at: timezone.datetime,
) -> None:
    update_defaults, create_defaults = _work_item_defaults(
        record,
        number=number,
        updated_at=updated_at,
    )
    if target_type == RepositoryWorkAssignment.TargetType.PULL_REQUEST:
        pull_request, was_created = RepositoryPullRequest.objects.get_or_create(
            repository=repository,
            number=number,
            defaults={
                **create_defaults,
                "merged_at": None,
                "source_branch": "",
                "target_branch": "",
                "is_draft": False,
            },
        )
        if not was_created:
            _update_existing_pull_request_fields(
                pull_request,
                update_defaults=update_defaults,
            )
    else:
        issue, was_created = RepositoryIssue.objects.get_or_create(
            repository=repository,
            number=number,
            defaults=create_defaults,
        )
        if not was_created:
            _update_existing_issue_fields(issue, update_defaults=update_defaults)


def _remove_assignment_record(
    *,
    repository: GitHubRepository,
    target_type: str,
    number: int,
    node: Node,
    updated_at: timezone.datetime,
) -> int:
    return (
        RepositoryWorkAssignment.objects.filter(
            repository=repository,
            target_type=target_type,
            number=number,
            node=node,
        )
        .exclude(status=RepositoryWorkAssignment.Status.REMOVED)
        .update(
            patchwork_authorized=False,
            status=RepositoryWorkAssignment.Status.REMOVED,
            updated_at=updated_at,
        )
    )


def _upsert_assignment_record(
    *,
    record: Mapping[str, Any],
    repository: GitHubRepository,
    target_type: str,
    number: int,
    node: Node,
    assigned_at: timezone.datetime,
    updated_at: timezone.datetime,
    status: str,
) -> str | None:
    values = {
        "patchwork_authorized": _patchwork_authorized(
            record.get("patchwork_authorized")
        ),
        "reason": str(record.get("reason") or "Synced from upstream node."),
        "status": status,
        "assigned_at": assigned_at,
        "updated_at": updated_at,
    }
    lookup = {
        "repository": repository,
        "target_type": target_type,
        "number": number,
        "node": node,
    }
    assignment = RepositoryWorkAssignment.objects.filter(**lookup).first()
    if assignment is None:
        assignment = RepositoryWorkAssignment.objects.create(
            **lookup,
            patchwork_authorized=values["patchwork_authorized"],
            reason=values["reason"],
            status=values["status"],
        )
        RepositoryWorkAssignment.objects.filter(pk=assignment.pk).update(
            assigned_at=assigned_at,
            updated_at=updated_at,
        )
        return "created"

    if all(getattr(assignment, field) == value for field, value in values.items()):
        return None

    RepositoryWorkAssignment.objects.filter(pk=assignment.pk).update(**values)
    return "updated"


def _apply_assignment_record(
    record: Mapping[str, Any],
    *,
    node: Node,
) -> str | None:
    if _json_object(record.get("node_fit")).get("eligible") is False:
        record = {**record, "status": RepositoryWorkAssignment.Status.REMOVED}
    context = _assignment_record_context(record)
    if context is None:
        return None
    repository, number, target_type, assigned_at, updated_at, status = context
    if status == RepositoryWorkAssignment.Status.REMOVED:
        removed = _remove_assignment_record(
            repository=repository,
            target_type=target_type,
            number=number,
            node=node,
            updated_at=updated_at,
        )
        return "removed" if removed else None

    _upsert_work_item(
        repository=repository,
        target_type=target_type,
        number=number,
        record=record,
        updated_at=updated_at,
    )
    return _upsert_assignment_record(
        record=record,
        repository=repository,
        target_type=target_type,
        number=number,
        node=node,
        assigned_at=assigned_at,
        updated_at=updated_at,
        status=status,
    )


def apply_assignment_payload(
    payload: Mapping[str, Any], *, node: Node | None = None
) -> dict[str, int]:
    """Apply upstream assignments to the local node."""

    if not isinstance(payload, Mapping):
        raise AssignmentSyncError("invalid assignment payload")
    assignments = payload.get("assignments")
    if assignments is None:
        assignments = []
    elif not isinstance(assignments, list):
        raise AssignmentSyncError("invalid assignment payload")

    target_node = node or Node.get_local()
    if target_node is None:
        raise AssignmentSyncError("local node is not registered")

    result = {"created": 0, "updated": 0, "removed": 0}
    for record in assignments:
        if not isinstance(record, Mapping):
            continue
        record_result = _apply_assignment_record(
            record,
            node=target_node,
        )
        if record_result in result:
            result[record_result] += 1

    return result


def _memory_info() -> dict[str, int | float]:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, raw_value = line.split(":", 1)
                amount = int(raw_value.strip().split()[0]) * 1024
                values[key] = amount
    except (OSError, ValueError):
        return {}
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used_percent = round(((total - available) / total) * 100, 1) if total else 0.0
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_percent": used_percent,
    }


def _load_average_info() -> dict[str, int | float]:
    try:
        one, five, fifteen = os.getloadavg()
    except (AttributeError, OSError):
        one = five = fifteen = 0.0
    return {
        "one": one,
        "five": five,
        "fifteen": fifteen,
        "cpu_count": os.cpu_count() or 1,
    }


def _disk_usage(path: str):
    try:
        return shutil.disk_usage(path)
    except OSError:
        return None


def _capability_terms_from_node_features(node_features: Iterable[str]) -> list[str]:
    terms: list[str] = []
    for feature in node_features:
        slug = str(feature or "").strip()
        if not slug:
            continue
        terms.append(slug)
        terms.extend(NODE_FEATURE_CAPABILITY_ALIASES.get(slug, ()))
    return sorted(dict.fromkeys(terms))


def local_developer_snapshot() -> dict[str, Any]:
    """Return this node's developer-facing capability snapshot."""

    local_node = Node.get_local()
    hostname = socket.gethostname()
    patchwork_path = resolve_patchwork_dir()
    patchwork_dir = str(patchwork_path)
    disk = _disk_usage(patchwork_dir)
    node_features: list[str] = []
    role = ""
    if local_node is not None:
        node_features = list(
            local_node.features.order_by("slug").values_list("slug", flat=True)
        )
        role = str(local_node_role() or "").strip() or (
            getattr(getattr(local_node, "role", None), "name", "") or ""
        )
    capability_terms = _capability_terms_from_node_features(node_features)
    try:
        from apps.features.models import Feature

        suite_features = list(
            Feature.objects.filter(is_enabled=True)
            .order_by("slug")
            .values_list("slug", flat=True)
        )
    except (OperationalError, ProgrammingError):
        suite_features = []

    return {
        "schema_version": 1,
        "reported_at": timezone.now().isoformat(),
        "node": {
            "hostname": getattr(local_node, "hostname", "") or hostname,
            "public_endpoint": getattr(local_node, "public_endpoint", "") or hostname,
            "uuid": str(getattr(local_node, "uuid", "") or ""),
        },
        "capabilities": {
            "node_role": role,
            "node_features": node_features,
            "suite_features": suite_features,
            "capability_terms": sorted(dict.fromkeys(capability_terms)),
            "patchwork_dir": patchwork_dir,
        },
        "current_load": {
            "load_average": _load_average_info(),
            "memory": _memory_info(),
            "patchwork_disk": (
                {
                    "path": patchwork_dir,
                    "total_bytes": disk.total,
                    "free_bytes": disk.free,
                    "used_percent": round(
                        ((disk.total - disk.free) / disk.total) * 100,
                        1,
                    ),
                }
                if disk is not None
                else {}
            ),
            "assigned_work": (
                RepositoryWorkAssignment.objects.filter(
                    node=local_node,
                    status__in=(
                        RepositoryWorkAssignment.Status.ASSIGNED,
                        RepositoryWorkAssignment.Status.ACTIVE,
                    ),
                ).count()
                if local_node is not None
                else 0
            ),
            "active_patchwork": (
                RepositoryWorkAssignment.objects.filter(
                    node=local_node,
                    patchwork_authorized=True,
                    status=RepositoryWorkAssignment.Status.ACTIVE,
                ).count()
                if local_node is not None
                else 0
            ),
        },
        "developer_info": {
            "base_dir": str(getattr(settings, "BASE_DIR", "")),
            "node_role": role,
            "hostname": hostname,
        },
    }


def pull_assignments_from_upstream(
    *,
    upstream_url: str | None = None,
    token: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Post local developer info upstream and apply returned assignments."""

    configured_url = upstream_url or configured_upstream_url()
    configured_token = token or configured_sync_token()
    if not configured_url or not configured_token:
        return {"enabled": False, "created": 0, "updated": 0, "removed": 0}

    url = assignment_sync_url(configured_url)
    response = requests.post(
        url,
        json=local_developer_snapshot(),
        headers={ASSIGNMENT_SYNC_HEADER: configured_token},
        timeout=timeout or configured_timeout(),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise AssignmentSyncError("invalid assignment payload")
    result = apply_assignment_payload(payload)
    return {"enabled": True, "url": url, **result}
