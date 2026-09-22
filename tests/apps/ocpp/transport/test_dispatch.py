from datetime import timedelta

from asgiref.sync import sync_to_async
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.ocpp.models import InboundProtocolRequest
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallError, CallResult
from apps.ocpp.protocol.replay import replay_policy_for_action
from apps.ocpp.services.replay import acquire_inbound_request
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger


class FrameDispatcherReplayTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("dispatcher-replay")

    async def test_exact_retransmission_replays_without_running_handler_twice(self) -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"status": "Accepted"}

        dispatcher = FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=lambda action: handler if action == "Heartbeat" else None,
        )
        frame = Call(
            unique_id="call-1",
            action="Heartbeat",
            payload={},
        )

        first = await dispatcher.dispatch(frame)
        second = await dispatcher.dispatch(frame)

        self.assertEqual(calls, 1)
        self.assertEqual(
            first,
            CallResult(unique_id="call-1", payload={"status": "Accepted"}),
        )
        self.assertEqual(second, first)

    async def test_reused_call_id_with_changed_payload_runs_new_request(self) -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"echo": payload["data"]}

        dispatcher = FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=lambda action: handler if action == "Heartbeat" else None,
        )

        first = await dispatcher.dispatch(
            Call(
                unique_id="reused",
                action="Heartbeat",
                payload={"data": "one"},
            )
        )
        second = await dispatcher.dispatch(
            Call(
                unique_id="reused",
                action="Heartbeat",
                payload={"data": "two"},
            )
        )

        self.assertEqual(calls, 2)
        self.assertEqual(first.payload, {"echo": "one"})
        self.assertEqual(second.payload, {"echo": "two"})

    async def test_formation_violation_is_stored_and_replayed(self) -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            raise ValueError("invalid")

        dispatcher = FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=lambda action: handler if action == "Heartbeat" else None,
        )
        frame = Call(
            unique_id="call-error",
            action="Heartbeat",
            payload={},
        )

        first = await dispatcher.dispatch(frame)
        second = await dispatcher.dispatch(frame)

        expected = CallError(
            unique_id="call-error",
            code="FormationViolation",
            description="Invalid payload.",
            details={},
        )
        self.assertEqual(calls, 1)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)

    async def test_existing_processing_request_does_not_run_handler_again(self) -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {}

        frame = Call(
            unique_id="in-flight",
            action="DataTransfer",
            payload={"vendorId": "vendor"},
        )
        await sync_to_async(acquire_inbound_request)(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            action=frame.action,
            call_id=frame.unique_id,
            payload=frame.payload,
            policy=replay_policy_for_action(frame.action),
        )
        dispatcher = FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=lambda action: handler if action == "DataTransfer" else None,
        )

        response = await dispatcher.dispatch(frame)

        self.assertEqual(calls, 0)
        self.assertEqual(
            response,
            CallError(
                unique_id="in-flight",
                code="InternalError",
                description="Request is already processing.",
                details={},
            ),
        )

    @override_settings(OCPP_REPLAY_WINDOW_SECONDS=60)
    async def test_repeatable_action_replays_within_window(self) -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"currentTime": f"value-{calls}"}

        dispatcher = FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=lambda action: handler if action == "Heartbeat" else None,
        )
        frame = Call(unique_id="heartbeat-1", action="Heartbeat", payload={})

        first = await dispatcher.dispatch(frame)
        second = await dispatcher.dispatch(frame)

        self.assertEqual(calls, 1)
        self.assertEqual(second, first)

    @override_settings(OCPP_REPLAY_WINDOW_SECONDS=60)
    async def test_repeatable_action_same_call_and_payload_is_new_after_window(self) -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"currentTime": f"value-{calls}"}

        dispatcher = FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=lambda action: handler if action == "Heartbeat" else None,
        )
        frame = Call(unique_id="heartbeat-1", action="Heartbeat", payload={})
        first = await dispatcher.dispatch(frame)

        await sync_to_async(
            InboundProtocolRequest.objects.filter(
                charger=self.charger,
                action="Heartbeat",
                unique_id="heartbeat-1",
            ).update
        )(received_at=timezone.now() - timedelta(minutes=2))

        second = await dispatcher.dispatch(frame)

        self.assertEqual(calls, 2)
        self.assertNotEqual(second, first)
