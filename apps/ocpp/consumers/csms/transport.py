"""Transport/session mixin for CSMS websocket lifecycle and forwarding."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.utils import timezone

from apps.nodes.models import Node
from apps.forwarder.ocpp import forwarder
from apps.ocpp.forwarder_feature import ocpp_forwarder_enabled
from apps.ocpp.models import Charger

logger = logging.getLogger(__name__)


class CSMSTransportMixin:
    """Provide forwarding transport helpers for CSMSConsumer."""

    async def _ensure_forwarding_context(
        self, charger
    ) -> tuple[tuple[str, ...], int | None] | None:
        """Return forwarding configuration for ``charger`` when available."""
        if not await database_sync_to_async(ocpp_forwarder_enabled)(default=True):
            return None
        if not charger or not getattr(charger, "forwarded_to_id", None):
            return None

        def _resolve():
            from apps.ocpp.models import CPForwarder

            target_id = getattr(charger, "forwarded_to_id", None)
            if not target_id:
                return None
            qs = CPForwarder.objects.filter(target_node_id=target_id, enabled=True)
            source_id = getattr(charger, "node_origin_id", None)
            resolver = None
            if source_id:
                resolver = qs.filter(source_node_id=source_id).first()
            if resolver is None:
                resolver = qs.filter(source_node__isnull=True).first()
            if resolver is None:
                resolver = qs.first()
            if resolver is None:
                return None
            messages = tuple(resolver.get_forwarded_messages())
            return messages, resolver.pk

        return await database_sync_to_async(_resolve)()

    async def _record_forwarding_activity(
        self,
        *,
        charger_pk: int | None,
        forwarder_pk: int | None,
        timestamp: datetime,
    ) -> None:
        """Persist forwarding activity metadata for the provided charger."""
        if charger_pk is None and forwarder_pk is None:
            return

        def _update():
            if charger_pk:
                Charger.objects.filter(pk=charger_pk).update(forwarding_watermark=timestamp)
            if forwarder_pk:
                from apps.ocpp.models import CPForwarder

                CPForwarder.objects.filter(pk=forwarder_pk).update(
                    last_forwarded_at=timestamp,
                    is_running=True,
                )

        await database_sync_to_async(_update)()

    async def _reconnect_forwarding_session(
        self,
        charger,
        *,
        allowed_messages: tuple[str, ...] | None,
        forwarder_pk: int | None,
    ):
        """Attempt to re-establish a forwarding session for ``charger``."""
        if charger is None or not getattr(charger, "pk", None):
            return None, None

        def _refresh():
            return Charger.objects.select_related("forwarded_to").filter(pk=charger.pk).first()

        refreshed = await database_sync_to_async(_refresh)()
        if refreshed is None:
            return None, None

        target = getattr(refreshed, "forwarded_to", None)
        if target is None:
            return None, refreshed

        session = await sync_to_async(forwarder.connect_forwarding_session)(refreshed, target)
        if session is None:
            return None, refreshed
        session.forwarded_messages = allowed_messages
        session.forwarder_id = forwarder_pk
        return session, refreshed

    async def _forward_charge_point_message_legacy(self, action: str, raw: str) -> None:
        """Forward an OCPP message to the configured remote node when permitted."""
        if not await database_sync_to_async(ocpp_forwarder_enabled)(default=True):
            return
        if not action or not raw:
            return

        charger = self.aggregate_charger or self.charger
        if charger is None or not getattr(charger, "pk", None):
            return
        session = forwarder.get_session(charger.pk)
        if session is None or not session.is_connected:
            return

        allowed = getattr(session, "forwarded_messages", None)
        forwarder_pk = getattr(session, "forwarder_id", None)
        if allowed is None or (forwarder_pk is None and charger.forwarded_to_id):
            context = await self._ensure_forwarding_context(charger)
            if context is None:
                return
            allowed, forwarder_pk = context
            session.forwarded_messages = allowed
            session.forwarder_id = forwarder_pk

        if allowed is not None and action not in allowed:
            return

        try:
            await sync_to_async(session.connection.send)(
                self._wrap_forwarding_payload(charger, raw, direction="cp_to_csms")
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Failed to forward %s from charger %s via %s: %s",
                action,
                getattr(charger, "charger_id", charger.pk),
                getattr(session, "url", "unknown"),
                exc,
            )
            forwarder.remove_session(charger.pk)
            session, refreshed = await self._reconnect_forwarding_session(
                charger,
                allowed_messages=allowed,
                forwarder_pk=forwarder_pk,
            )
            if session is None:
                return
            context = await self._ensure_forwarding_context(refreshed)
            if context is None:
                forwarder.remove_session(charger.pk)
                return
            allowed, forwarder_pk = context
            session.forwarded_messages = allowed
            session.forwarder_id = forwarder_pk
            if allowed is not None and action not in allowed:
                return
            try:
                await sync_to_async(session.connection.send)(
                    self._wrap_forwarding_payload(charger, raw, direction="cp_to_csms")
                )
            except Exception as retry_exc:  # pragma: no cover
                logger.warning(
                    "Failed to forward %s from charger %s after reconnect: %s",
                    action,
                    getattr(charger, "charger_id", charger.pk),
                    retry_exc,
                )
                forwarder.remove_session(charger.pk)
                return

        timestamp = timezone.now()
        session.last_activity = timestamp
        await self._record_forwarding_activity(
            charger_pk=charger.pk,
            forwarder_pk=forwarder_pk,
            timestamp=timestamp,
        )
        charger.forwarding_watermark = timestamp
        aggregate = self.aggregate_charger
        if aggregate and aggregate.pk == charger.pk:
            aggregate.forwarding_watermark = timestamp
        current = self.charger
        if current and current.pk == charger.pk and current is not aggregate:
            current.forwarding_watermark = timestamp

    async def _forward_charge_point_message(self, action: str, raw: str) -> None:
        """Compatibility adapter to preserve charge-point forwarding entrypoint."""
        await self._forward_charge_point_message_legacy(action, raw)

    async def _forward_charge_point_reply_legacy(self, message_id: str, raw: str) -> None:
        """Forward a call result or error back to the remote node when needed."""
        if not await database_sync_to_async(ocpp_forwarder_enabled)(default=True):
            return
        if not message_id or not raw:
            return
        charger = self.aggregate_charger or self.charger
        if charger is None or not getattr(charger, "pk", None):
            return
        session = forwarder.get_session(charger.pk)
        if session is None or not session.is_connected:
            return
        with session._pending_lock:
            if message_id not in session.pending_call_ids:
                return
            session.pending_call_ids.discard(message_id)
        try:
            await sync_to_async(session.connection.send)(
                self._wrap_forwarding_payload(charger, raw, direction="cp_to_csms_reply")
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Failed to forward reply %s for charger %s via %s: %s",
                message_id,
                getattr(charger, "charger_id", charger.pk),
                getattr(session, "url", "unknown"),
                exc,
            )
            forwarder.remove_session(charger.pk)

    def _wrap_forwarding_payload(self, charger, raw: str, *, direction: str) -> str:
        """Wrap OCPP message with route metadata for forwarding channels."""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if not isinstance(payload, list):
            return raw
        local_node = Node.get_local()
        meta: dict[str, object] = {
            "charger_id": getattr(charger, "charger_id", None),
            "connector_id": getattr(charger, "connector_id", None),
            "direction": direction,
        }
        if local_node and getattr(local_node, "uuid", None):
            meta["route"] = [str(local_node.uuid)]
        return json.dumps({"ocpp": payload, "meta": meta})
