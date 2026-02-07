"""Transaction and reporting helpers for the OCPP store."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone as dt_timezone
import threading
from typing import Iterable

from .state import connector_slug, identity_key

transaction_requests: dict[str, dict[str, object]] = {}
_transaction_requests_by_connector: dict[str, set[str]] = {}
_transaction_requests_by_transaction: dict[str, set[str]] = {}
_transaction_requests_lock = threading.Lock()

billing_updates: deque[dict[str, object]] = deque(maxlen=1000)
ev_charging_needs: deque[dict[str, object]] = deque(maxlen=500)
ev_charging_schedules: deque[dict[str, object]] = deque(maxlen=500)
planner_notifications: deque[dict[str, object]] = deque(maxlen=500)
observability_events: deque[dict[str, object]] = deque(maxlen=1000)
transaction_events: deque[dict[str, object]] = deque(maxlen=1000)
connector_release_notifications: deque[dict[str, object]] = deque(maxlen=500)
monitoring_reports: deque[dict[str, object]] = deque(maxlen=1000)
display_message_compliance: dict[str, list[dict[str, object]]] = {}
charging_profile_reports: dict[str, dict[int, dict[str, object]]] = {}


def _normalize_transaction_id(value: object | None) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _transaction_connector_key(charger_id: str | None, connector: int | str | None) -> str | None:
    if not charger_id:
        return None
    return identity_key(charger_id, connector)


def _remove_transaction_index_entry(
    mapping: dict[str, set[str]], key: str | None, message_id: str
) -> None:
    if not key:
        return
    entries = mapping.get(key)
    if not entries:
        return
    entries.discard(message_id)
    if not entries:
        mapping.pop(key, None)


def _add_transaction_index_entry(
    mapping: dict[str, set[str]], key: str | None, message_id: str
) -> None:
    if not key:
        return
    mapping.setdefault(key, set()).add(message_id)


def register_transaction_request(message_id: str, metadata: dict[str, object]) -> None:
    """Register a transaction-related request for later reconciliation."""

    entry = dict(metadata)
    entry.setdefault("status", "requested")
    entry.setdefault("status_at", datetime.now(dt_timezone.utc))
    connector_key = _transaction_connector_key(
        str(entry.get("charger_id") or ""), entry.get("connector_id")
    )
    transaction_key = _normalize_transaction_id(
        entry.get("transaction_id") or entry.get("ocpp_transaction_id")
    )
    with _transaction_requests_lock:
        transaction_requests[message_id] = entry
        _add_transaction_index_entry(
            _transaction_requests_by_connector, connector_key, message_id
        )
        _add_transaction_index_entry(
            _transaction_requests_by_transaction, transaction_key, message_id
        )


def record_display_message_compliance(
    charger_id: str | None,
    *,
    request_id: int | None,
    tbc: bool,
    messages: list[dict[str, object]],
    received_at: datetime,
) -> None:
    """Track NotifyDisplayMessages payloads for compliance reporting."""

    if not charger_id:
        return
    record = {
        "charger_id": charger_id,
        "request_id": request_id,
        "tbc": tbc,
        "messages": messages,
        "received_at": received_at,
    }
    display_message_compliance.setdefault(charger_id, []).append(record)


def clear_display_message_compliance() -> None:
    """Clear cached NotifyDisplayMessages compliance data (test helper)."""

    display_message_compliance.clear()


def record_reported_charging_profile(
    charger_id: str | None,
    *,
    request_id: int | None,
    evse_id: int | str | None,
    profile_id: int | None,
) -> None:
    """Track profiles reported during a ReportChargingProfiles sequence."""

    if not charger_id or profile_id is None:
        return

    request_key = int(request_id) if request_id is not None else -1
    connector_key = connector_slug(evse_id)

    entry = charging_profile_reports.setdefault(charger_id, {}).setdefault(
        request_key, {"reported": {}}
    )
    entry["reported"].setdefault(connector_key, set()).add(profile_id)


def consume_reported_charging_profiles(
    charger_id: str | None, *, request_id: int | None
) -> dict[str, object] | None:
    """Pop recorded ReportChargingProfiles entries for the request."""

    if not charger_id:
        return None

    request_key = int(request_id) if request_id is not None else -1
    entries = charging_profile_reports.get(charger_id)
    if entries is None:
        return None

    record = entries.pop(request_key, None)
    if not entries:
        charging_profile_reports.pop(charger_id, None)

    if record is None:
        return None

    reported = record.get("reported") or {}
    normalized = {
        key: set(value) if isinstance(value, set) else set()
        for key, value in reported.items()
    }
    return {"reported": normalized}


def record_ev_charging_needs(
    charger_id: str | None,
    *,
    connector_id: int | str | None,
    evse_id: int,
    requested_energy: int | None,
    departure_time: datetime | None,
    charging_needs: dict[str, object] | None,
    received_at: datetime,
) -> None:
    """Track EV charging needs so schedulers can prioritize sessions."""

    if not charger_id:
        return

    record = {
        "charger_id": charger_id,
        "connector_id": connector_slug(connector_id),
        "evse_id": evse_id,
        "requested_energy": requested_energy,
        "departure_time": departure_time,
        "charging_needs": dict(charging_needs or {}),
        "received_at": received_at,
    }
    ev_charging_needs.append(record)


def record_ev_charging_schedule(
    charger_id: str | None,
    *,
    connector_id: int | str | None,
    evse_id: int,
    timebase: datetime | None,
    charging_schedule: dict[str, object] | None,
    received_at: datetime,
) -> None:
    """Track EV charging schedules so planners can synchronize demand."""

    if not charger_id or charging_schedule is None:
        return

    ev_charging_schedules.append(
        {
            "charger_id": charger_id,
            "connector_id": connector_slug(connector_id),
            "evse_id": evse_id,
            "timebase": timebase,
            "charging_schedule": dict(charging_schedule),
            "received_at": received_at,
        }
    )


def record_monitoring_report(
    charger_id: str | None,
    *,
    request_id: int | None,
    seq_no: int | None,
    generated_at: datetime | None,
    tbc: bool,
    component_name: str,
    component_instance: str,
    variable_name: str,
    variable_instance: str,
    monitoring_id: int,
    severity: int | None,
    monitor_type: str,
    threshold: str,
    is_transaction: bool,
    evse_id: int | None,
    connector_id: int | str | None,
    received_at: datetime,
) -> None:
    """Queue a normalized monitoring report entry for analytics pipelines."""

    if not charger_id:
        return

    monitoring_reports.append(
        {
            "charger_id": charger_id,
            "request_id": request_id,
            "seq_no": seq_no,
            "generated_at": generated_at,
            "tbc": tbc,
            "component_name": component_name,
            "component_instance": component_instance,
            "variable_name": variable_name,
            "variable_instance": variable_instance,
            "monitoring_id": monitoring_id,
            "severity": severity,
            "monitor_type": monitor_type,
            "threshold": threshold,
            "is_transaction": is_transaction,
            "evse_id": evse_id,
            "connector_id": connector_slug(connector_id),
            "received_at": received_at,
        }
    )


def forward_ev_charging_schedule(schedule: dict[str, object]) -> None:
    """Queue a normalized EV charging schedule for downstream planners."""

    planner_notifications.append(dict(schedule))


def forward_event_to_observability(event: dict[str, object]) -> None:
    """Queue a normalized NotifyEvent payload for observability pipelines."""

    observability_events.append(dict(event))


def record_transaction_event(event: dict[str, object]) -> None:
    """Queue a normalized TransactionEvent payload for downstream handlers."""

    transaction_events.append(dict(event))


def forward_connector_release(notification: dict[str, object]) -> None:
    """Queue a connector release notification for reservation workflows."""

    connector_release_notifications.append(dict(notification))


def update_transaction_request(
    message_id: str,
    *,
    status: str | None = None,
    connector_id: int | str | None = None,
    transaction_id: str | int | None = None,
    ocpp_transaction_id: str | int | None = None,
) -> dict[str, object] | None:
    """Update metadata for a tracked transaction request."""

    with _transaction_requests_lock:
        entry = transaction_requests.get(message_id)
        if not entry:
            return None
        if status:
            entry["status"] = status
            entry["status_at"] = datetime.now(dt_timezone.utc)
        if connector_id is not None and connector_slug(entry.get("connector_id")) != connector_slug(
            connector_id
        ):
            old_key = _transaction_connector_key(
                str(entry.get("charger_id") or ""), entry.get("connector_id")
            )
            new_key = _transaction_connector_key(
                str(entry.get("charger_id") or ""), connector_id
            )
            _remove_transaction_index_entry(
                _transaction_requests_by_connector, old_key, message_id
            )
            _add_transaction_index_entry(
                _transaction_requests_by_connector, new_key, message_id
            )
            entry["connector_id"] = connector_id
        if transaction_id is not None or ocpp_transaction_id is not None:
            old_tx_key = _normalize_transaction_id(
                entry.get("transaction_id") or entry.get("ocpp_transaction_id")
            )
            new_tx_key = _normalize_transaction_id(
                transaction_id if transaction_id is not None else ocpp_transaction_id
            )
            if new_tx_key and new_tx_key != old_tx_key:
                _remove_transaction_index_entry(
                    _transaction_requests_by_transaction, old_tx_key, message_id
                )
                _add_transaction_index_entry(
                    _transaction_requests_by_transaction, new_tx_key, message_id
                )
                entry["transaction_id"] = new_tx_key
        return dict(entry)


def find_transaction_requests(
    *,
    charger_id: str,
    connector_id: int | str | None = None,
    transaction_id: str | int | None = None,
    action: str | None = None,
    statuses: set[str] | None = None,
) -> list[tuple[str, dict[str, object]]]:
    """Return tracked transaction requests matching the supplied filters."""

    connector_key = _transaction_connector_key(charger_id, connector_id)
    transaction_key = _normalize_transaction_id(transaction_id)
    candidates: set[str] = set()
    with _transaction_requests_lock:
        if transaction_key:
            candidates.update(
                _transaction_requests_by_transaction.get(transaction_key, set())
            )
        if connector_key:
            candidates.update(
                _transaction_requests_by_connector.get(connector_key, set())
            )
        if not transaction_key and not connector_key:
            candidates.update(
                message_id
                for message_id, entry in transaction_requests.items()
                if entry.get("charger_id") == charger_id
            )
        results: list[tuple[str, dict[str, object]]] = []
        for message_id in candidates:
            entry = transaction_requests.get(message_id)
            if not entry:
                continue
            if entry.get("charger_id") != charger_id:
                continue
            if connector_id is not None and connector_slug(entry.get("connector_id")) != connector_slug(
                connector_id
            ):
                continue
            if action and entry.get("action") != action:
                continue
            if statuses and entry.get("status") not in statuses:
                continue
            results.append((message_id, dict(entry)))
    results.sort(
        key=lambda item: item[1].get("requested_at")
        or item[1].get("status_at")
        or datetime.min.replace(tzinfo=dt_timezone.utc),
        reverse=True,
    )
    return results


def mark_transaction_requests(
    *,
    charger_id: str,
    connector_id: int | str | None = None,
    transaction_id: str | int | None = None,
    actions: Iterable[str] | None = None,
    statuses: set[str] | None = None,
    status: str,
) -> list[dict[str, object]]:
    """Update matching transaction requests and return the updated entries."""

    actions_set = set(actions or [])
    matches = find_transaction_requests(
        charger_id=charger_id,
        connector_id=connector_id,
        transaction_id=transaction_id,
    )
    updated: list[dict[str, object]] = []
    for message_id, entry in matches:
        if actions_set and entry.get("action") not in actions_set:
            continue
        if statuses and entry.get("status") not in statuses:
            continue
        update = update_transaction_request(
            message_id,
            status=status,
            connector_id=connector_id,
            transaction_id=transaction_id,
        )
        if update:
            updated.append(update)
    return updated


def forward_cost_update_to_billing(update: dict[str, object]) -> None:
    """Queue a cost update payload for downstream billing handlers."""

    billing_updates.append(dict(update))


__all__ = [
    "_add_transaction_index_entry",
    "_normalize_transaction_id",
    "_remove_transaction_index_entry",
    "_transaction_connector_key",
    "_transaction_requests_by_connector",
    "_transaction_requests_by_transaction",
    "_transaction_requests_lock",
    "billing_updates",
    "charging_profile_reports",
    "clear_display_message_compliance",
    "connector_release_notifications",
    "consume_reported_charging_profiles",
    "display_message_compliance",
    "ev_charging_needs",
    "ev_charging_schedules",
    "find_transaction_requests",
    "forward_connector_release",
    "forward_cost_update_to_billing",
    "forward_ev_charging_schedule",
    "forward_event_to_observability",
    "mark_transaction_requests",
    "monitoring_reports",
    "observability_events",
    "planner_notifications",
    "record_display_message_compliance",
    "record_ev_charging_needs",
    "record_ev_charging_schedule",
    "record_monitoring_report",
    "record_reported_charging_profile",
    "record_transaction_event",
    "register_transaction_request",
    "transaction_events",
    "transaction_requests",
    "update_transaction_request",
]
