"""Explicit outbound OCPP calls with completion correlation."""

from collections.abc import Awaitable, Callable

from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call
from apps.ocpp.protocol.registry import resolve_action

SendJson = Callable[[list[object]], Awaitable[None]]


class OutboundSender:
    """Emit one supported CSMS call and await its matching completion."""

    def __init__(
        self,
        *,
        version: ProtocolVersion,
        send_json: SendJson,
        pending_calls: PendingCalls,
    ) -> None:
        self.version = version
        self.send_json = send_json
        self.pending_calls = pending_calls

    async def send(
        self,
        *,
        action: str,
        payload: dict[str, object],
        timeout: float = 30,
        unique_id: str | None = None,
    ) -> dict[str, object]:
        """Send a supported outbound action without scheduling charger work."""
        if (
            resolve_action(
                version=self.version,
                direction=Direction.CSMS_TO_CHARGE_POINT,
                action=action,
            )
            is None
        ):
            raise ValueError(f"Unsupported outbound OCPP action: {action}")
        call_id, future = self.pending_calls.open(unique_id)
        await self.send_json(Call(call_id, action, payload).to_wire())
        return await self.pending_calls.wait(call_id, future, timeout)
