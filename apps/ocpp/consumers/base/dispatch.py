import base64
import json
from functools import cached_property
from typing import assert_never

from ... import store
from ...call_error_handlers import dispatch_call_error
from ...call_result_handlers import dispatch_call_result
from ...models import Charger
from ..csms.protocol import (
    OCPPCallEnvelope,
    OCPPCallErrorEnvelope,
    OCPPCallResultEnvelope,
    validate_call_envelope,
    validate_message_envelope,
)
from .routing import ActionRouter


class DispatchMixin:

    @cached_property
    def _action_router(self) -> ActionRouter:
        """Cache action router for the life of a websocket consumer instance."""

        return ActionRouter(self)

    async def receive(self, text_data=None, bytes_data=None):
        raw = self._normalize_raw_message(text_data, bytes_data)
        if raw is None:
            return
        store.add_log(self.store_key, raw, log_type="charger")
        store.add_session_message(self.store_key, raw)
        msg = self._parse_message(raw)
        if msg is None:
            return
        envelope = validate_message_envelope(msg)
        if envelope is None:
            return
        if isinstance(envelope, OCPPCallEnvelope):
            await self._handle_call_message(envelope, raw, text_data)
        elif isinstance(envelope, OCPPCallResultEnvelope):
            await self._handle_call_result(envelope.message_id, envelope.payload, raw)
        elif isinstance(envelope, OCPPCallErrorEnvelope):
            await self._handle_call_error(
                envelope.message_id,
                envelope.error_code,
                envelope.description,
                envelope.details,
                raw,
            )
        else:  # pragma: no cover - exhaustiveness guard for static typing
            assert_never(envelope)

    def _normalize_raw_message(self, text_data, bytes_data):
        raw = text_data
        if raw is None and bytes_data is not None:
            raw = base64.b64encode(bytes_data).decode("ascii")
        return raw

    def _parse_message(self, raw: str) -> list[object] | None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(msg, list) or not msg:
            return None
        return msg

    async def _handle_call_message(self, msg: object, raw, text_data):
        envelope = (
            msg if isinstance(msg, OCPPCallEnvelope) else validate_call_envelope(msg)
        )
        if envelope is None:
            return
        msg_id, action, payload = envelope.message_id, envelope.action, envelope.payload
        connector_hint = payload.get("connectorId")
        self._log_triggered_follow_up(action, connector_hint)
        await self._assign_connector(payload.get("connectorId"))
        reply_payload = {}
        handler = self._action_router.resolve(action)
        if handler:
            reply_payload = await handler(payload, msg_id, raw, text_data)
        response = [3, msg_id, reply_payload]
        await self.send(json.dumps(response))
        store.add_log(self.store_key, f"< {json.dumps(response)}", log_type="charger")

    def _log_triggered_follow_up(self, action: str, connector_hint):
        follow_up = store.consume_triggered_followup(
            self.charger_id, action, connector_hint
        )
        if not follow_up:
            return
        follow_up_log_key = follow_up.get("log_key") or self.store_key
        target_label = follow_up.get("target") or action
        connector_slug_value = follow_up.get("connector")
        suffix = ""
        if connector_slug_value and connector_slug_value != store.AGGREGATE_SLUG:
            connector_letter = Charger.connector_letter_from_slug(connector_slug_value)
            if connector_letter:
                suffix = f" (connector {connector_letter})"
            else:
                suffix = f" (connector {connector_slug_value})"
        store.add_log(
            follow_up_log_key,
            f"TriggerMessage follow-up received: {target_label}{suffix}",
            log_type="charger",
        )

    async def _handle_call_result(
        self, message_id: str, payload: dict | None, raw: str | None = None
    ) -> None:
        metadata = store.pop_pending_call(message_id)
        if not metadata:
            return
        metadata_charger = metadata.get("charger_id")
        if metadata_charger and self.charger_id:
            metadata_serial = Charger.normalize_serial(str(metadata_charger)).casefold()
            consumer_serial = Charger.normalize_serial(self.charger_id).casefold()
            if (
                metadata_serial
                and consumer_serial
                and metadata_serial != consumer_serial
            ):
                return
        action = metadata.get("action")
        log_key = metadata.get("log_key") or self.store_key
        payload_data = payload if isinstance(payload, dict) else {}
        handled = await dispatch_call_result(
            self,
            action,
            message_id,
            metadata,
            payload_data,
            log_key,
        )
        if handled:
            return
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            payload=payload_data,
        )

    async def _handle_call_error(
        self,
        message_id: str,
        error_code: str | None,
        description: str | None,
        details: dict | None,
        raw: str | None = None,
    ) -> None:
        metadata = store.pop_pending_call(message_id)
        if not metadata:
            return
        metadata_charger = metadata.get("charger_id")
        if metadata_charger and self.charger_id:
            metadata_serial = Charger.normalize_serial(str(metadata_charger)).casefold()
            consumer_serial = Charger.normalize_serial(self.charger_id).casefold()
            if (
                metadata_serial
                and consumer_serial
                and metadata_serial != consumer_serial
            ):
                return
        action = metadata.get("action")
        log_key = metadata.get("log_key") or self.store_key
        handled = await dispatch_call_error(
            self,
            action,
            message_id,
            metadata,
            error_code,
            description,
            details,
            log_key,
        )
        if handled:
            return
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            success=False,
            error_code=error_code,
            error_description=description,
            error_details=details,
        )


__all__ = ["DispatchMixin"]
