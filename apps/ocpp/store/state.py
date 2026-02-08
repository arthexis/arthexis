"""Stateful caches and connection helpers for the OCPP store."""

from __future__ import annotations

from collections import deque
from datetime import datetime

from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError

IDENTITY_SEPARATOR = "#"
AGGREGATE_SLUG = "all"
PENDING_SLUG = "pending"

MAX_CONNECTIONS_PER_IP = 2

_STATE_REDIS: Redis | None = None
_STATE_REDIS_URL = getattr(settings, "OCPP_STATE_REDIS_URL", "")
_IP_CONNECTION_TTL = 3600


_UNSET = object()


def configure_redis_for_testing(
    *, redis_client: Redis | None | object = _UNSET, redis_url: str | object = _UNSET
) -> None:
    """Override cached Redis state for tests."""

    global _STATE_REDIS, _STATE_REDIS_URL
    if redis_client is not _UNSET:
        _STATE_REDIS = redis_client
    if redis_url is not _UNSET:
        _STATE_REDIS_URL = redis_url


def _state_redis() -> Redis | None:
    global _STATE_REDIS
    if not _STATE_REDIS_URL:
        return None
    if _STATE_REDIS is None:
        try:
            _STATE_REDIS = Redis.from_url(_STATE_REDIS_URL, decode_responses=True)
        except Exception:  # pragma: no cover - best effort fallback
            _STATE_REDIS = None
    return _STATE_REDIS


connections: dict[str, object] = {}
transactions: dict[str, object] = {}
# store per charger session logs before they are flushed to disk
simulators: dict[str, object] = {}
ip_connections: dict[str, set[object]] = {}

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


def connector_slug(value: int | str | None) -> str:
    """Return the canonical slug for a connector value."""

    if value in (None, "", AGGREGATE_SLUG):
        return AGGREGATE_SLUG
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def identity_key(serial: str, connector: int | str | None) -> str:
    """Return the identity key used for in-memory store lookups."""

    return f"{serial}{IDENTITY_SEPARATOR}{connector_slug(connector)}"


def pending_key(serial: str) -> str:
    """Return the key used before a connector id has been negotiated."""

    return f"{serial}{IDENTITY_SEPARATOR}{PENDING_SLUG}"


def _connection_token(consumer: object) -> str:
    token = getattr(consumer, "_ocpp_state_token", None)
    if token:
        return token
    token = getattr(consumer, "channel_name", None) or f"consumer-{id(consumer)}"
    try:
        setattr(consumer, "_ocpp_state_token", token)
    except Exception:  # pragma: no cover - best effort
        pass
    return token


def _redis_ip_key(ip: str) -> str:
    return f"ocpp:ip-connection:{ip}"


def _register_ip_connection_redis(ip: str, consumer: object) -> bool | None:
    client = _state_redis()
    if not client:
        return None
    key = _redis_ip_key(ip)
    token = _connection_token(consumer)
    try:
        pipe = client.pipeline()
        pipe.sadd(key, token)
        pipe.expire(key, _IP_CONNECTION_TTL)
        pipe.scard(key)
        added, _expired, count = pipe.execute()
        if count > MAX_CONNECTIONS_PER_IP and added:
            client.srem(key, token)
            return False
        return count <= MAX_CONNECTIONS_PER_IP
    except RedisError:
        return None


def _release_ip_connection_redis(ip: str, consumer: object) -> None:
    client = _state_redis()
    if not client:
        return
    key = _redis_ip_key(ip)
    token = _connection_token(consumer)
    try:
        client.srem(key, token)
    except RedisError:
        return


def register_ip_connection(ip: str | None, consumer: object) -> bool:
    """Track a websocket connection for the provided client IP."""

    if not ip:
        return True
    allowed = _register_ip_connection_redis(ip, consumer)
    if allowed is False:
        return False
    conns = ip_connections.setdefault(ip, set())
    if consumer in conns:
        return True
    if len(conns) >= MAX_CONNECTIONS_PER_IP:
        if allowed:
            _release_ip_connection_redis(ip, consumer)
        return False
    conns.add(consumer)
    return True


def release_ip_connection(ip: str | None, consumer: object) -> None:
    """Remove a websocket connection from the active client registry."""

    if not ip:
        return
    _release_ip_connection_redis(ip, consumer)
    conns = ip_connections.get(ip)
    if not conns:
        return
    conns.discard(consumer)
    if not conns:
        ip_connections.pop(ip, None)


def _candidate_keys(serial: str, connector: int | str | None) -> list[str]:
    """Return possible keys for lookups with fallbacks."""

    keys: list[str] = []
    if connector not in (None, "", AGGREGATE_SLUG):
        keys.append(identity_key(serial, connector))
    else:
        keys.append(identity_key(serial, None))
        prefix = f"{serial}{IDENTITY_SEPARATOR}"
        for key in connections.keys():
            if key.startswith(prefix) and key not in keys:
                keys.append(key)
    keys.append(pending_key(serial))
    keys.append(serial)
    seen: set[str] = set()
    result: list[str] = []
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def iter_identity_keys(serial: str) -> list[str]:
    """Return all known keys for the provided serial."""

    prefix = f"{serial}{IDENTITY_SEPARATOR}"
    keys = [key for key in connections.keys() if key.startswith(prefix)]
    if serial in connections:
        keys.append(serial)
    return keys


def is_connected(serial: str, connector: int | str | None = None) -> bool:
    """Return whether a connection exists for the provided charger identity."""

    if connector in (None, "", AGGREGATE_SLUG):
        prefix = f"{serial}{IDENTITY_SEPARATOR}"
        return (
            any(key.startswith(prefix) for key in connections) or serial in connections
        )
    return any(key in connections for key in _candidate_keys(serial, connector))


def get_connection(serial: str, connector: int | str | None = None):
    """Return the websocket consumer for the requested identity, if any."""

    for key in _candidate_keys(serial, connector):
        conn = connections.get(key)
        if conn is not None:
            return conn
    return None


def set_connection(serial: str, connector: int | str | None, consumer) -> str:
    """Store a websocket consumer under the negotiated identity."""

    key = identity_key(serial, connector)
    connections[key] = consumer
    return key


def pop_connection(serial: str, connector: int | str | None = None):
    """Remove a stored connection for the given identity."""

    for key in _candidate_keys(serial, connector):
        conn = connections.pop(key, None)
        if conn is not None:
            return conn
    return None


def get_transaction(serial: str, connector: int | str | None = None):
    """Return the active transaction for the provided identity."""

    for key in _candidate_keys(serial, connector):
        tx = transactions.get(key)
        if tx is not None:
            return tx
    return None


def set_transaction(serial: str, connector: int | str | None, tx) -> str:
    """Store an active transaction under the provided identity."""

    key = identity_key(serial, connector)
    transactions[key] = tx
    return key


def pop_transaction(serial: str, connector: int | str | None = None):
    """Remove and return an active transaction for the identity."""

    for key in _candidate_keys(serial, connector):
        tx = transactions.pop(key, None)
        if tx is not None:
            return tx
    return None


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


def forward_cost_update_to_billing(update: dict[str, object]) -> None:
    """Queue a cost update payload for downstream billing handlers."""

    billing_updates.append(dict(update))


__all__ = [
    "AGGREGATE_SLUG",
    "IDENTITY_SEPARATOR",
    "MAX_CONNECTIONS_PER_IP",
    "PENDING_SLUG",
    "billing_updates",
    "charging_profile_reports",
    "clear_display_message_compliance",
    "configure_redis_for_testing",
    "connector_release_notifications",
    "connector_slug",
    "connections",
    "consume_reported_charging_profiles",
    "display_message_compliance",
    "ev_charging_needs",
    "ev_charging_schedules",
    "forward_connector_release",
    "forward_cost_update_to_billing",
    "forward_ev_charging_schedule",
    "forward_event_to_observability",
    "get_connection",
    "get_transaction",
    "identity_key",
    "ip_connections",
    "is_connected",
    "iter_identity_keys",
    "monitoring_reports",
    "observability_events",
    "pending_key",
    "planner_notifications",
    "pop_connection",
    "pop_transaction",
    "record_display_message_compliance",
    "record_ev_charging_needs",
    "record_ev_charging_schedule",
    "record_monitoring_report",
    "record_reported_charging_profile",
    "record_transaction_event",
    "register_ip_connection",
    "release_ip_connection",
    "set_connection",
    "set_transaction",
    "simulators",
    "transaction_events",
    "transactions",
]
