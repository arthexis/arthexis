"""Thin Channels consumer that delegates OCPP behavior to protocol packages."""

import asyncio

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.errors import ProtocolFrameError
from apps.ocpp.protocol.frames import CallError, parse_frame
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.services.compatibility import record_compatibility_evidence
from apps.ocpp.services.discovery_handoff import claim_and_record_discovery_handoff
from apps.ocpp.services.presence import touch_connection
from apps.ocpp.transport.connection import (
    basic_credentials,
    load_or_enroll_charger,
    negotiate_subprotocol,
)
from apps.ocpp.transport.dispatch import FrameDispatcher
from apps.ocpp.transport.listener import trusted_charger_listener
from apps.ocpp.transport.operations import (
    connection_capacity_available,
    deliver_queued_operation,
    recover_connected_operations,
    register_connection,
    unregister_connection,
)
from apps.ocpp.transport.sender import OutboundSender


class CSMSConsumer(AsyncJsonWebsocketConsumer):
    """Connect, parse, dispatch, and send without embedding OCPP action logic."""

    async def connect(self) -> None:
        offered_subprotocols = self.scope.get("subprotocols", [])
        self.subprotocol, self.version = negotiate_subprotocol(offered_subprotocols)

        identity = self.scope["url_route"]["kwargs"]["charger_identity"]
        credentials = basic_credentials(self.scope.get("headers", []))
        self.charger = await load_or_enroll_charger(
            identity,
            credentials,
            trusted_listener=trusted_charger_listener(self.scope),
        )
        if self.charger is None:
            await self.close(code=4401)
            return

        protocol_fallback = self.subprotocol not in offered_subprotocols
        if protocol_fallback:
            await sync_to_async(record_compatibility_evidence)(
                kind="protocol_fallback",
                charger=self.charger,
                protocol=self.subprotocol,
                details={"offered_subprotocols": offered_subprotocols},
            )

        if not await connection_capacity_available(self.charger):
            await sync_to_async(record_compatibility_evidence)(
                kind="connection_capacity",
                charger=self.charger,
                protocol=self.subprotocol,
                details={"limit": "reached"},
            )
            await self.close(code=1013)
            return

        self.pending_calls = PendingCalls()
        inbound_actions = (
            InboundActions(self.charger)
            if self.subprotocol == "ocpp1.6"
            else Inbound201Actions(self.charger)
        )
        self.dispatcher = FrameDispatcher(
            charger=self.charger,
            version=self.version,
            pending_calls=self.pending_calls,
            handler_resolver=inbound_actions.resolve,
        )
        self.outbound = OutboundSender(
            version=self.version,
            send_json=self.send_json,
            pending_calls=self.pending_calls,
        )
        await register_connection(
            charger=self.charger,
            sender=self.outbound,
            channel_name=self.channel_name,
            version=self.version,
        )
        await self.accept(
            subprotocol=None if protocol_fallback else self.subprotocol
        )
        await sync_to_async(claim_and_record_discovery_handoff)(
            charger=self.charger,
            scope=self.scope,
            protocol=self.subprotocol,
            offered_subprotocols=list(offered_subprotocols),
        )
        asyncio.create_task(
            recover_connected_operations(
                charger=self.charger,
                sender=self.outbound,
                version=self.version,
                delivery_owner=self.channel_name,
            )
        )

    async def disconnect(self, close_code: int) -> None:
        if hasattr(self, "pending_calls"):
            self.pending_calls.close()
            await unregister_connection(
                charger=self.charger,
                channel_name=self.channel_name,
            )

    async def ocpp_emit(self, event: dict[str, object]) -> None:
        """Deliver one explicitly queued operation without blocking frame receipt."""
        operation_id = event.get("operation_id")
        timeout = event.get("timeout", 30)
        if not isinstance(operation_id, int) or not isinstance(timeout, (int, float)):
            return
        asyncio.create_task(
            deliver_queued_operation(
                charger=self.charger,
                sender=self.outbound,
                version=self.version,
                operation_id=operation_id,
                timeout=float(timeout),
                delivery_owner=self.channel_name,
            )
        )

    async def receive_json(self, content, **kwargs) -> None:
        try:
            frame = parse_frame(content)
        except ProtocolFrameError as error:
            unique_id = (
                content[1]
                if isinstance(content, list)
                and len(content) > 1
                and isinstance(content[1], str)
                else ""
            )
            await sync_to_async(record_compatibility_evidence)(
                kind="malformed_frame",
                charger=self.charger,
                protocol=self.subprotocol,
                unique_id=unique_id,
                details={
                    "error_code": error.code,
                    "description": error.description,
                    "frame": content,
                },
            )
            await self.send_json(
                CallError(unique_id, error.code, error.description, {}).to_wire()
            )
            return
        await sync_to_async(touch_connection)(
            charger=self.charger,
            channel_name=self.channel_name,
        )
        try:
            response = await self.dispatcher.dispatch(frame)
        except ProtocolFrameError as error:
            await self.send_json(
                CallError("", error.code, error.description, {}).to_wire()
            )
            return
        if response is not None:
            await self.send_json(response.to_wire())
