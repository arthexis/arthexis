"""Registry-based dispatch for inbound OCPP frames."""

from collections.abc import Awaitable, Callable

from django.core.exceptions import ObjectDoesNotExist

from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallError, CallResult, Frame
from apps.ocpp.protocol.registry import resolve_action

ActionHandler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]
HandlerResolver = Callable[[str], ActionHandler | None]


class FrameDispatcher:
    """Route Calls by registry and settle correlated outbound completions."""

    def __init__(
        self,
        *,
        version: ProtocolVersion,
        pending_calls: PendingCalls,
        handler_resolver: HandlerResolver,
    ) -> None:
        self.version = version
        self.pending_calls = pending_calls
        self.handler_resolver = handler_resolver

    async def dispatch(self, frame: Frame) -> CallResult | CallError | None:
        if isinstance(frame, (CallResult, CallError)):
            self.pending_calls.resolve(frame)
            return None

        contract = resolve_action(
            version=self.version,
            direction=Direction.CHARGE_POINT_TO_CSMS,
            action=frame.action,
        )
        if contract is None:
            return CallError(
                unique_id=frame.unique_id,
                code="NotSupported",
                description=f"Unsupported action: {frame.action}",
                details={},
            )

        handler = self.handler_resolver(frame.action)
        if handler is None:
            return CallError(
                unique_id=frame.unique_id,
                code="NotSupported",
                description=f"Action not implemented: {frame.action}",
                details={},
            )
        try:
            return CallResult(
                unique_id=frame.unique_id, payload=await handler(frame.payload)
            )
        except (KeyError, ObjectDoesNotExist, TypeError, ValueError):
            return CallError(
                unique_id=frame.unique_id,
                code="FormationViolation",
                description="Invalid payload.",
                details={},
            )
