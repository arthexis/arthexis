"""In-memory store for OCPP data with file backed logs."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, cast

from apps.ocpp.payload_types import PendingCallMetadata

from . import (
    logs as logs_module,
    pending_calls as pending_calls_module,
    scheduler as scheduler_module,
    state as state_module,
    transactions as transactions_module,
)

_modules = (
    state_module,
    logs_module,
    pending_calls_module,
    transactions_module,
    scheduler_module,
)

_PUBLIC_FUNCTION_NAMES = tuple(
    name
    for module in _modules
    for name in getattr(module, "__all__", [])
    if inspect.isfunction(getattr(module, name, None))
)

logs = logs_module.logs
pending_calls = pending_calls_module.pending_calls
transactions = state_module.transactions


def register_pending_call(
    message_id: str, metadata: Mapping[str, object]
) -> None:
    """Store metadata about an outstanding CSMS call."""

    pending_calls_module.register_pending_call(message_id, dict(metadata))


def pop_pending_call(message_id: str) -> PendingCallMetadata | None:
    """Return typed metadata for a previously registered call."""

    return cast(
        PendingCallMetadata | None,
        pending_calls_module.pop_pending_call(message_id),
    )


def record_pending_call_result(
    message_id: str,
    *,
    metadata: Mapping[str, object] | None = None,
    success: bool = True,
    payload: object | None = None,
    error_code: str | None = None,
    error_description: str | None = None,
    error_details: object | None = None,
) -> None:
    """Record the outcome for a previously registered pending call."""

    pending_calls_module.record_pending_call_result(
        message_id,
        metadata=dict(metadata) if metadata is not None else None,
        success=success,
        payload=payload,
        error_code=error_code,
        error_description=error_description,
        error_details=error_details,
    )


def reassign_identity(old_key: str, new_key: str) -> str:
    """Move any stored data from ``old_key`` to ``new_key``."""

    if old_key == new_key:
        return new_key
    if not old_key:
        return new_key
    for mapping in (
        state_module.connections,
        state_module.transactions,
        logs_module.history,
    ):
        if old_key in mapping:
            mapping[new_key] = mapping.pop(old_key)
    for log_type in logs_module.logs:
        store_map = logs_module.logs[log_type]
        if old_key in store_map:
            store_map[new_key] = store_map.pop(old_key)
    for log_type in logs_module.log_names:
        names = logs_module.log_names[log_type]
        if old_key in names:
            names[new_key] = names.pop(old_key)
    return new_key


__all__ = sorted([*_PUBLIC_FUNCTION_NAMES, "reassign_identity"])


def __getattr__(name: str) -> Any:
    for module in _modules:
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
