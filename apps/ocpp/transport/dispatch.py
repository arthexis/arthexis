"""Registry-based dispatch for inbound OCPP frames."""

from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist

from apps.ocpp.models import Charger, InboundProtocolRequest
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallError, CallResult, Frame
from apps.ocpp.protocol.registry import resolve_action
from apps.ocpp.protocol.replay import replay_policy_for_action
from apps.ocpp.services.replay import (
    acquire_inbound_request,
    complete_with_error,
    complete_with_result,
    stored_response,
)

ActionHandler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]
HandlerResolver = Callable[[str], ActionHandler | None]


class FrameDispatcher:
    """Route Calls by registry and settle correlated outbound completions."""

    def __init__(
        self,
        *,
        charger: Charger,
        version: ProtocolVersion,
        pending_calls: PendingCalls,
        handler_resolver: HandlerResolver,
    ) -> None:
        self.charger = charger
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

        acquired = await sync_to_async(acquire_inbound_request)(
            charger=self.charger,
            version=self.version,
            action=frame.action,
            call_id=frame.unique_id,
            payload=frame.payload,
            policy=replay_policy_for_action(frame.action),
        )
        if acquired.completed:
            return stored_response(acquired.request, call_id=frame.unique_id)
        if acquired.stale:
            return CallError(
                unique_id=frame.unique_id,
                code="InternalError",
                description="Request recovery is required.",
                details={},
            )
        if not acquired.created:
            return CallError(
                unique_id=frame.unique_id,
                code="InternalError",
                description="Request is already processing.",
                details={},
            )

        try:
            response = CallResult(
                unique_id=frame.unique_id,
                payload=await handler(frame.payload),
            )
        except (KeyError, ObjectDoesNotExist, TypeError, ValueError):
            response = CallError(
                unique_id=frame.unique_id,
                code="FormationViolation",
                description="Invalid payload.",
                details={},
            )

        await self._complete(acquired.request, response)
        return response

    async def _complete(
        self,
        request: InboundProtocolRequest,
        response: CallResult | CallError,
    ) -> None:
        if isinstance(response, CallResult):
            await sync_to_async(complete_with_result)(
                request,
                payload=response.payload,
            )
            return
        await sync_to_async(complete_with_error)(
            request,
            code=response.code,
            description=response.description,
            details=response.details,
        )
