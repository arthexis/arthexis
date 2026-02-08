"""Call result handlers for configuration actions."""
from __future__ import annotations

import json

from channels.db import database_sync_to_async

from ... import store
from ...models import ChargerConfiguration
from ..types import CallResultContext


async def handle_change_configuration_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    key_value = str(metadata.get("key") or "").strip()
    status_value = str(payload_data.get("status") or "").strip()
    stored_value = metadata.get("value")
    parts: list[str] = []
    if status_value:
        parts.append(f"status={status_value}")
    if key_value:
        parts.append(f"key={key_value}")
    if stored_value is not None:
        parts.append(f"value={stored_value}")
    message = "ChangeConfiguration result"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    if status_value.casefold() in {"accepted", "rebootrequired"} and key_value:
        connector_hint = metadata.get("connector_id")

        def _apply() -> ChargerConfiguration:
            return consumer._apply_change_configuration_snapshot(
                key_value,
                stored_value if isinstance(stored_value, str) else None,
                connector_hint,
            )

        configuration = await database_sync_to_async(_apply)()
        if configuration:
            if getattr(consumer, "charger", None) and getattr(consumer, "charger_id", None):
                if getattr(consumer.charger, "charger_id", None) == consumer.charger_id:
                    consumer.charger.configuration = configuration
            if getattr(consumer, "aggregate_charger", None) and getattr(
                consumer, "charger_id", None
            ):
                if (
                    getattr(consumer.aggregate_charger, "charger_id", None)
                    == consumer.charger_id
                ):
                    consumer.aggregate_charger.configuration = configuration
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_configuration_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    try:
        payload_text = json.dumps(payload_data, sort_keys=True, ensure_ascii=False)
    except TypeError:
        payload_text = str(payload_data)
    store.add_log(
        log_key,
        f"GetConfiguration result: {payload_text}",
        log_type="charger",
    )
    configuration = await database_sync_to_async(consumer._persist_configuration_result)(
        payload_data, metadata.get("connector_id")
    )
    if configuration:
        if getattr(consumer, "charger", None) and getattr(consumer, "charger_id", None):
            if getattr(consumer.charger, "charger_id", None) == consumer.charger_id:
                consumer.charger.configuration = configuration
        if getattr(consumer, "aggregate_charger", None) and getattr(
            consumer, "charger_id", None
        ):
            if (
                getattr(consumer.aggregate_charger, "charger_id", None)
                == consumer.charger_id
            ):
                consumer.aggregate_charger.configuration = configuration
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True
