"""Thin Channels consumer that delegates OCPP behavior to protocol packages."""

import asyncio

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.errors import ProtocolFrameError
from apps.ocpp.protocol.frames import CallError, parse_frame
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.transport.connection import (
    ConnectionRejected,
    basic_credentials,
    load_or_enroll_charger,
    negotiate_subprotocol,
)
from apps.ocpp.transport.dispatch import FrameDispatcher
from apps.ocpp.transport.operations import (
    deliver_queued_operation,
    register_connection,
    unregister_connection,
)
from apps.ocpp.transport.sender import OutboundSender


class CSMSConsumer(AsyncJsonWebsocketConsumer):
    """Connect, parse, dispatch, and send without embedding OCPP action logic."""

    async def connect(self) -> None:
        try:
            self.subprotocol, self.version = negotiate_subprotocol(
                self.scope.get("subprotocols", [])
            )
        except ConnectionRejected:
            await self.close(code=4406)
            return

        identity = self.scope["url_route"]["kwargs"]["charger_identity"]
        credentials = basic_credentials(self.scope.get("headers", []))
        self.charger = await load_or_enroll_charger(identity, credentials)
        if self.charger is None:
            await self.close(code=4401)
            return

        self.pending_calls = PendingCalls()
        inbound_actions = (
            InboundActions(self.charger)
            if self.subprotocol == "ocpp1.6"
            else Inbound201Actions(self.charger)
        )
        self.dispatcher = FrameDispatcher(
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
        await self.accept(subprotocol=self.subprotocol)

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
            )
        )

    async def receive_json(self, content, **kwargs) -> None:
        try:
            response = await self.dispatcher.dispatch(parse_frame(content))
        except ProtocolFrameError as error:
            await self.send_json(
                CallError("", error.code, error.description, {}).to_wire()
            )
            return
        if response is not None:
            await self.send_json(response.to_wire())
