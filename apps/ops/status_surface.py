"""Status surface builders for operational health and scoped log excerpts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.models import Charger, ControlOperationEvent
from apps.ops.models import SecurityAlertEvent

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
REDACTION_SENTINEL = "[REDACTED]"

_SECRET_FIELD_PATTERN = re.compile(
    r'(?P<key>"?[\w-]*?(?:password|passphrase|token|api[_-]?key|authorization|secret)[\w-]*"?\s*[:=]\s*)'
    r'(?P<value>"[^"\\]*(?:\\.[^"\\]*)*"|\S+)',
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Z0-9._+/=-]+")
_BASIC_PATTERN = re.compile(r"(?i)\bBasic\s+[A-Z0-9._+/=-]+")


@dataclass(frozen=True)
class StatusCondition:
    """Single health condition with operator guidance."""

    code: str
    severity: str
    summary: str
    guidance: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "guidance": self.guidance,
        }


def _redact_value(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        return f'"{REDACTION_SENTINEL}"'
    return REDACTION_SENTINEL


def redact_log_line(raw_line: str) -> str:
    """Redact secrets from a single log line."""

    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", raw_line)
    redacted = _BASIC_PATTERN.sub("Basic [REDACTED]", redacted)

    def _replace_field(match: re.Match[str]) -> str:
        return f"{match.group('key')}{_redact_value(match.group('value'))}"

    redacted = _SECRET_FIELD_PATTERN.sub(_replace_field, redacted)

    if ":" in redacted:
        prefix, _, payload = redacted.partition(":")
        payload = payload.strip()
        if payload.startswith("{") and payload.endswith("}"):
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return redacted
            _redact_payload_mapping(data)
            return f"{prefix}: {json.dumps(data, ensure_ascii=False)}"

    return redacted


def _redact_payload_mapping(payload: dict[str, object]) -> None:
    for key, value in list(payload.items()):
        lowered = str(key).lower()
        if any(token in lowered for token in ("password", "token", "secret", "authorization", "api_key", "apikey")):
            payload[key] = REDACTION_SENTINEL
        elif isinstance(value, dict):
            _redact_payload_mapping(value)
        elif isinstance(value, list):
            payload[key] = [REDACTION_SENTINEL if isinstance(item, str) and "bearer " in item.lower() else item for item in value]


def _visible_chargers(user) -> Iterable[Charger]:
    return Charger.visible_for_user(user).only("id", "charger_id", "connector_id")


def _is_staff_scope(user) -> bool:
    return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


def _scope_queue_counts(*, user, visible: list[Charger]) -> tuple[int, int]:
    if _is_staff_scope(user):
        return len(store.pending_calls), len(store.monitoring_report_requests)

    visible_keys = {store.identity_key(charger.charger_id, charger.connector_id) for charger in visible}
    visible_pairs = {(charger.charger_id, charger.connector_id) for charger in visible}
    pending_calls = sum(
        1 for metadata in store.pending_calls.values() if metadata.get("log_key") in visible_keys
    )
    monitoring_requests = sum(
        1
        for metadata in store.monitoring_report_requests.values()
        if (
            metadata.get("charger_id"),
            metadata.get("connector_id"),
        )
        in visible_pairs
    )
    return pending_calls, monitoring_requests


def _status_role(user) -> str:
    if getattr(user, "is_superuser", False):
        return "superuser"
    if getattr(user, "is_staff", False):
        return "staff"
    return "tenant"


def _guidance_for_connectivity(connected: int, total: int) -> StatusCondition:
    if total == 0:
        return StatusCondition(
            code="ocpp_connectivity",
            severity=SEVERITY_WARNING,
            summary="No visible charge points are configured for this scope.",
            guidance="Confirm tenant ownership scope and register at least one charge point before triage.",
        )
    ratio = connected / total
    if ratio < 0.5:
        return StatusCondition(
            code="ocpp_connectivity",
            severity=SEVERITY_CRITICAL,
            summary=f"Only {connected}/{total} visible charge points are connected.",
            guidance="Check WebSocket reachability, node routing, and charger network health immediately.",
        )
    if ratio < 0.9:
        return StatusCondition(
            code="ocpp_connectivity",
            severity=SEVERITY_WARNING,
            summary=f"Partial connectivity: {connected}/{total} visible charge points online.",
            guidance="Review offline connectors and verify heartbeat cadence before capacity is impacted.",
        )
    return StatusCondition(
        code="ocpp_connectivity",
        severity=SEVERITY_INFO,
        summary=f"Connectivity healthy: {connected}/{total} visible charge points online.",
        guidance="Continue normal monitoring.",
    )


def _guidance_for_backlog(pending_calls: int) -> StatusCondition:
    if pending_calls >= 50:
        return StatusCondition(
            code="ocpp_pending_calls",
            severity=SEVERITY_CRITICAL,
            summary=f"High OCPP command backlog ({pending_calls} pending calls).",
            guidance="Investigate blocked call-result handling and broker latency; throttle new remote operations.",
        )
    if pending_calls >= 10:
        return StatusCondition(
            code="ocpp_pending_calls",
            severity=SEVERITY_WARNING,
            summary=f"Elevated OCPP command backlog ({pending_calls} pending calls).",
            guidance="Inspect command throughput and timeout rates.",
        )
    return StatusCondition(
        code="ocpp_pending_calls",
        severity=SEVERITY_INFO,
        summary=f"Backlog nominal ({pending_calls} pending calls).",
        guidance="No immediate queue intervention required.",
    )


def _guidance_for_failures(failure_count: int) -> StatusCondition:
    if failure_count >= 10:
        severity = SEVERITY_CRITICAL
        summary = f"Frequent critical failures in the last 24h ({failure_count})."
        guidance = "Escalate incident response, inspect recent deploys/integrations, and stabilize failing controls."
    elif failure_count > 0:
        severity = SEVERITY_WARNING
        summary = f"Recent failures detected in the last 24h ({failure_count})."
        guidance = "Review failed operation details and remediate recurring causes."
    else:
        severity = SEVERITY_INFO
        summary = "No critical failures detected in the last 24h."
        guidance = "Continue routine verification and alert monitoring."
    return StatusCondition(
        code="recent_failures",
        severity=severity,
        summary=summary,
        guidance=guidance,
    )


def _critical_events_for_scope(*, user, limit: int = 10) -> list[dict[str, object]]:
    failed_ops = _failed_operations_for_scope(user=user).select_related("charger")[:limit]
    since = timezone.now() - timedelta(hours=24)
    alerts = SecurityAlertEvent.objects.none()
    if _is_staff_scope(user):
        alerts = SecurityAlertEvent.objects.filter(
            is_active=True,
            last_occurred_at__gte=since,
            severity__in=["error", "critical"],
        )[:limit]
    items: list[dict[str, object]] = []
    for event in failed_ops:
        items.append(
            {
                "source": "control_operation",
                "severity": SEVERITY_CRITICAL,
                "timestamp": event.created_at,
                "summary": f"{event.charger.charger_id} {event.action} failed",
                "details": redact_log_line(event.detail or "Operation failed"),
            }
        )
    for alert in alerts:
        items.append(
            {
                "source": "security_alert",
                "severity": SEVERITY_CRITICAL,
                "timestamp": alert.last_occurred_at,
                "summary": alert.message,
                "details": redact_log_line(alert.detail or ""),
            }
        )
    return sorted(items, key=lambda item: item["timestamp"], reverse=True)[:limit]


def _failed_operations_for_scope(*, user):
    since = timezone.now() - timedelta(hours=24)
    visible_charger_ids = list(_visible_chargers(user).values_list("id", flat=True))
    return ControlOperationEvent.objects.filter(
        created_at__gte=since,
        status=ControlOperationEvent.Status.FAILED,
        charger_id__in=visible_charger_ids,
    )


def scoped_log_excerpts(*, user, limit_per_charger: int = 5) -> list[dict[str, object]]:
    """Return role-aware, tenant-scoped log excerpts with redaction."""

    include_sensitive_event_names = _is_staff_scope(user)
    excerpts: list[dict[str, object]] = []
    for charger in _visible_chargers(user).order_by("charger_id", "connector_id")[:20]:
        key = store.identity_key(charger.charger_id, charger.connector_id)
        lines = []
        for entry in store.iter_log_entries(key, log_type="charger", limit=limit_per_charger * 3):
            text = entry.text
            if (not include_sensitive_event_names) and any(
                marker in text
                for marker in (
                    "SecurityEventNotification",
                    "Authorize processed:",
                    "DataTransfer received:",
                )
            ):
                continue
            lines.append(
                {
                    "timestamp": entry.timestamp,
                    "line": redact_log_line(text),
                }
            )
            if len(lines) >= limit_per_charger:
                break
        if lines:
            excerpts.append(
                {
                    "charger_id": charger.charger_id,
                    "connector": charger.connector_id,
                    "entries": lines,
                }
            )
    return excerpts


def build_status_surface(*, user) -> dict[str, object]:
    """Build the consolidated status payload for operator surfaces."""

    visible = list(_visible_chargers(user))
    connected = sum(
        1
        for charger in visible
        if store.is_connected(charger.charger_id, charger.connector_id)
    )
    pending_count, monitoring_request_count = _scope_queue_counts(user=user, visible=visible)
    failure_count = _failed_operations_for_scope(user=user).count()
    critical_events = _critical_events_for_scope(user=user)
    conditions = [
        _guidance_for_connectivity(connected, len(visible)),
        _guidance_for_backlog(pending_count),
        _guidance_for_failures(failure_count),
    ]

    return {
        "generated_at": timezone.now(),
        "scope": {
            "role": _status_role(user),
            "visible_chargers": len(visible),
            "sensitive_events_visible": _is_staff_scope(user),
        },
        "service_health": {
            "ocpp_websocket": {
                "connected": connected,
                "total": len(visible),
            },
            "queue": {
                "pending_calls": pending_count,
                "monitoring_requests": monitoring_request_count,
            },
        },
        "recent_critical_events": critical_events,
        "status_conditions": [condition.to_dict() for condition in conditions],
        "log_excerpts": scoped_log_excerpts(user=user),
    }
