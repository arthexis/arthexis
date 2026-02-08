"""Transaction request indexing helpers for the OCPP store."""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
import threading
from typing import Iterable

from . import state

transaction_requests: dict[str, dict[str, object]] = {}
_transaction_requests_by_connector: dict[str, set[str]] = {}
_transaction_requests_by_transaction: dict[str, set[str]] = {}
_transaction_requests_lock = threading.Lock()


def _normalize_transaction_id(value: object | None) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _transaction_connector_key(charger_id: str | None, connector: int | str | None) -> str | None:
    if not charger_id:
        return None
    return state.identity_key(charger_id, connector)


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
        if connector_id is not None and state.connector_slug(entry.get("connector_id")) != state.connector_slug(
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
        results: list[tuple[str, dict[str, object]]] = []
        for message_id in candidates:
            entry = transaction_requests.get(message_id)
            if not entry:
                continue
            if entry.get("charger_id") != charger_id:
                continue
            if connector_id is not None and state.connector_slug(entry.get("connector_id")) != state.connector_slug(
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


__all__ = [
    "_add_transaction_index_entry",
    "_normalize_transaction_id",
    "_remove_transaction_index_entry",
    "_transaction_connector_key",
    "_transaction_requests_by_connector",
    "_transaction_requests_by_transaction",
    "_transaction_requests_lock",
    "find_transaction_requests",
    "mark_transaction_requests",
    "register_transaction_request",
    "transaction_requests",
    "update_transaction_request",
]
