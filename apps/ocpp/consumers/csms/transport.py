"""Transport/session mixin for CSMS websocket lifecycle and forwarding."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.utils import timezone

from apps.nodes.models import Node
from apps.ocpp.forwarder import forwarder
from apps.ocpp.forwarder_feature import ocpp_forwarder_enabled
from apps.ocpp.models import Charger

logger = logging.getLogger(__name__)


class CSMSTransportMixin:
    """Provide forwarding transport helpers for CSMSConsumer."""

    _FORWARDING_LOCAL_NODE_UNSET = object()

    @staticmethod
    def _forwarding_interval_seconds(session) -> float:
        interval = getattr(session, "forwarding_interval_seconds", 0.0) or 0.0
        try:
            return max(0.0, float(interval))
        except (TypeError, ValueError):
            return 0.0

    async def _flush_buffered_forward_messages(self, session, *, now: datetime) -> bool:
        lock = getattr(session, "_cp_messages_lock", None)
        if lock is None:
            return False
        with lock:
            session._cp_flush_handle = None
            pending = dict(getattr(session, "pending_cp_messages", {}))
            if not pending:
                return False
        forwarded = False
        for action, payload in pending.items():
            await sync_to_async(session.connection.send)(payload)
            forwarded = True
            with lock:
                if session.pending_cp_messages.get(action) == payload:
                    session.pending_cp_messages.pop(action, None)
        with lock:
            session.last_cp_flush_at = now
        return forwarded

    async def _send_or_buffer_cp_payload(
        self,
        *,
        session,
        action: str,
        wrapped_payload: str,
    ) -> bool:
        """Forward immediately or enqueue based on session forwarding interval."""
        interval_seconds = self._forwarding_interval_seconds(session)
        if interval_seconds <= 0:
            self._cancel_scheduled_cp_flush(session)
            await self._flush_buffered_forward_messages(session, now=timezone.now())
            await sync_to_async(session.connection.send)(wrapped_payload)
            return True

        lock = getattr(session, "_cp_messages_lock", None)
        if lock is None:
            await sync_to_async(session.connection.send)(wrapped_payload)
            return True

        now = timezone.now()
        with lock:
            session.pending_cp_messages[action] = wrapped_payload
            last_flush = getattr(session, "last_cp_flush_at", None)
            should_flush = last_flush is None or (now - last_flush).total_seconds() >= interval_seconds
        if should_flush:
            return await self._flush_buffered_forward_messages(
                session,
                now=now,
            )
        self._schedule_cp_flush(
            session,
            interval_seconds=interval_seconds,
        )
        return False

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

        forwarded = False
        try:
            wrapped_payload = await self._wrap_forwarding_payload(
                charger,
                raw,
                direction="cp_to_csms",
            )
            forwarded = await self._send_or_buffer_cp_payload(
                session=session,
                action=action,
                wrapped_payload=wrapped_payload,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Failed to forward %s from charger %s via %s: %s",
                action,
                getattr(charger, "charger_id", charger.pk),
                getattr(session, "url", "unknown"),
                exc,
            )
            preserved_pending: dict[str, str] = {}
            lock = getattr(session, "_cp_messages_lock", None)
            if lock is not None:
                with lock:
                    preserved_pending = dict(getattr(session, "pending_cp_messages", {}))
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
            lock = getattr(session, "_cp_messages_lock", None)
            if lock is not None and preserved_pending:
                with lock:
                    for pending_action, payload in preserved_pending.items():
                        session.pending_cp_messages.setdefault(pending_action, payload)
            if allowed is not None and action not in allowed:
                return
            try:
                wrapped_payload = await self._wrap_forwarding_payload(
                    charger,
                    raw,
                    direction="cp_to_csms",
                )
                forwarded = await self._send_or_buffer_cp_payload(
                    session=session,
                    action=action,
                    wrapped_payload=wrapped_payload,
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

        if not forwarded:
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
            wrapped_payload = await self._wrap_forwarding_payload(
                charger,
                raw,
                direction="cp_to_csms_reply",
            )
            await sync_to_async(session.connection.send)(wrapped_payload)
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Failed to forward reply %s for charger %s via %s: %s",
                message_id,
                getattr(charger, "charger_id", charger.pk),
                getattr(session, "url", "unknown"),
                exc,
            )
            forwarder.remove_session(charger.pk)

    async def _wrap_forwarding_payload(self, charger, raw: str, *, direction: str) -> str:
        """Wrap OCPP message with route metadata for forwarding channels."""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if not isinstance(payload, list):
            return raw
        local_node = await self._get_local_node_for_forwarding()
        meta: dict[str, object] = {
            "charger_id": getattr(charger, "charger_id", None),
            "connector_id": getattr(charger, "connector_id", None),
            "direction": direction,
        }
        if local_node and getattr(local_node, "uuid", None):
            meta["route"] = [str(local_node.uuid)]
        return json.dumps({"ocpp": payload, "meta": meta})

    async def _get_local_node_for_forwarding(self):
        cached_local_node = getattr(
            self,
            "_forwarding_local_node",
            self._FORWARDING_LOCAL_NODE_UNSET,
        )
        if cached_local_node is self._FORWARDING_LOCAL_NODE_UNSET:
            cached_local_node = await database_sync_to_async(Node.get_local)()
            self._forwarding_local_node = cached_local_node
        return cached_local_node

    @staticmethod
    def _cancel_scheduled_cp_flush(session) -> None:
        lock = getattr(session, "_cp_messages_lock", None)
        if lock is None:
            return
        with lock:
            handle = getattr(session, "_cp_flush_handle", None)
            session._cp_flush_handle = None
        if handle is not None:
            handle.cancel()

    def _schedule_cp_flush(self, session, *, interval_seconds: float) -> None:
        lock = getattr(session, "_cp_messages_lock", None)
        if lock is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def _run_scheduled_flush() -> None:
            loop.create_task(self._run_scheduled_cp_flush(session))

        with lock:
            existing = getattr(session, "_cp_flush_handle", None)
            if existing is not None and not existing.cancelled():
                return
            session._cp_flush_handle = loop.call_later(interval_seconds, _run_scheduled_flush)

    async def _run_scheduled_cp_flush(self, session) -> None:
        try:
            await self._flush_buffered_forward_messages(session, now=timezone.now())
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Scheduled flush failed for charger %s via %s: %s",
                getattr(session, "charger_pk", "unknown"),
                getattr(session, "url", "unknown"),
                exc,
            )
