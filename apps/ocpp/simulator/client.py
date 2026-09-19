"""A small in-process OCPP charge-point client for protocol scenarios."""

from apps.ocpp.models import Charger
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallError, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions as V16InboundActions
from apps.ocpp.protocol.v201.inbound import InboundActions as V201InboundActions
from apps.ocpp.transport.dispatch import FrameDispatcher


class OcppSimulator:
    """Send retained inbound calls as a protocol client, not a host service."""

    def __init__(self, *, charger: Charger, version: ProtocolVersion) -> None:
        handlers = (
            V16InboundActions(charger)
            if version == ProtocolVersion.OCPP_16
            else V201InboundActions(charger)
        )
        self._dispatcher = FrameDispatcher(
            version=version,
            pending_calls=PendingCalls(),
            handler_resolver=handlers.resolve,
        )
        self._sequence = 0

    async def call(self, action: str, payload: dict[str, object]) -> CallResult:
        """Send one inbound call and require a correlated accepted frame."""
        self._sequence += 1
        response = await self._dispatcher.dispatch(
            Call(unique_id=f"sim-{self._sequence}", action=action, payload=payload)
        )
        if isinstance(response, CallError):
            raise ValueError(f"{action}: {response.code}: {response.description}")
        if not isinstance(response, CallResult):
            raise RuntimeError(f"{action}: no protocol response")
        return response
