"""Shared in-memory state and identity helpers for OCPP."""

from __future__ import annotations

IDENTITY_SEPARATOR = "#"
AGGREGATE_SLUG = "all"
PENDING_SLUG = "pending"

MAX_CONNECTIONS_PER_IP = 2

connections: dict[str, object] = {}
transactions: dict[str, object] = {}
simulators: dict[str, object] = {}
ip_connections: dict[str, set[object]] = {}


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


def reassign_identity(old_key: str, new_key: str) -> str:
    """Move any stored data from ``old_key`` to ``new_key``."""

    if old_key == new_key:
        return new_key
    if not old_key:
        return new_key
    from . import logs as log_store

    for mapping in (connections, transactions, log_store.history):
        if old_key in mapping:
            mapping[new_key] = mapping.pop(old_key)

    for log_type in log_store.logs:
        store = log_store.logs[log_type]
        if old_key in store:
            store[new_key] = store.pop(old_key)
    for log_type in log_store.log_names:
        names = log_store.log_names[log_type]
        if old_key in names:
            names[new_key] = names.pop(old_key)
    return new_key


__all__ = [
    "IDENTITY_SEPARATOR",
    "AGGREGATE_SLUG",
    "PENDING_SLUG",
    "MAX_CONNECTIONS_PER_IP",
    "connections",
    "transactions",
    "simulators",
    "ip_connections",
    "connector_slug",
    "identity_key",
    "pending_key",
    "_candidate_keys",
    "iter_identity_keys",
    "is_connected",
    "get_connection",
    "set_connection",
    "pop_connection",
    "get_transaction",
    "set_transaction",
    "pop_transaction",
    "reassign_identity",
]
