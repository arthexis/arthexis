from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.test import override_settings
from django.utils import timezone

from apps.ocpp.models import InboundProtocolRequest
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallError, CallResult
from apps.ocpp.protocol.replay import replay_policy_for_action
from apps.ocpp.services.replay import acquire_inbound_request
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


@pytest.fixture
def replay_charger():
    return charger("dispatcher-replay")


def run(coro):
    return async_to_sync(coro)()


def make_dispatcher(selected, handler):
    return FrameDispatcher(
        charger=selected,
        version=ProtocolVersion.OCPP_16,
        pending_calls=PendingCalls(),
        handler_resolver=lambda action: handler if action in {"Heartbeat", "DataTransfer"} else None,
    )


def test_exact_retransmission_replays_without_running_handler_twice(
    replay_charger,
) -> None:
    async def scenario() -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"status": "Accepted"}

        dispatcher = make_dispatcher(replay_charger, handler)
        frame = Call(unique_id="call-1", action="Heartbeat", payload={})

        first = await dispatcher.dispatch(frame)
        second = await dispatcher.dispatch(frame)

        assert calls == 1
        assert first == CallResult(
            unique_id="call-1",
            payload={"status": "Accepted"},
        )
        assert second == first

    run(scenario)


def test_reused_call_id_with_changed_payload_runs_new_request(
    replay_charger,
) -> None:
    async def scenario() -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"echo": payload["data"]}

        dispatcher = make_dispatcher(replay_charger, handler)

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

        assert calls == 2
        assert first.payload == {"echo": "one"}
        assert second.payload == {"echo": "two"}

    run(scenario)


def test_formation_violation_is_stored_and_replayed(replay_charger) -> None:
    async def scenario() -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            raise ValueError("invalid")

        dispatcher = make_dispatcher(replay_charger, handler)
        frame = Call(unique_id="call-error", action="Heartbeat", payload={})

        first = await dispatcher.dispatch(frame)
        second = await dispatcher.dispatch(frame)

        expected = CallError(
            unique_id="call-error",
            code="FormationViolation",
            description="Invalid payload.",
            details={},
        )
        assert calls == 1
        assert first == expected
        assert second == expected

    run(scenario)


def test_existing_processing_request_does_not_run_handler_again(
    replay_charger,
) -> None:
    async def scenario() -> None:
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
            charger=replay_charger,
            version=ProtocolVersion.OCPP_16,
            action=frame.action,
            call_id=frame.unique_id,
            payload=frame.payload,
            policy=replay_policy_for_action(frame.action),
        )
        dispatcher = make_dispatcher(replay_charger, handler)

        response = await dispatcher.dispatch(frame)

        assert calls == 0
        assert response == CallError(
            unique_id="in-flight",
            code="InternalError",
            description="Request is already processing.",
            details={},
        )

    run(scenario)


@override_settings(OCPP_REPLAY_WINDOW_SECONDS=60)
def test_repeatable_action_replays_within_window(replay_charger) -> None:
    async def scenario() -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"currentTime": f"value-{calls}"}

        dispatcher = make_dispatcher(replay_charger, handler)
        frame = Call(unique_id="heartbeat-1", action="Heartbeat", payload={})

        first = await dispatcher.dispatch(frame)
        second = await dispatcher.dispatch(frame)

        assert calls == 1
        assert second == first

    run(scenario)


@override_settings(OCPP_REPLAY_WINDOW_SECONDS=60)
def test_repeatable_action_same_call_and_payload_is_new_after_window(
    replay_charger,
) -> None:
    async def scenario() -> None:
        calls = 0

        async def handler(payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"currentTime": f"value-{calls}"}

        dispatcher = make_dispatcher(replay_charger, handler)
        frame = Call(unique_id="heartbeat-1", action="Heartbeat", payload={})
        first = await dispatcher.dispatch(frame)

        await sync_to_async(
            InboundProtocolRequest.objects.filter(
                charger=replay_charger,
                action="Heartbeat",
                unique_id="heartbeat-1",
            ).update
        )(received_at=timezone.now() - timedelta(minutes=2))

        second = await dispatcher.dispatch(frame)

        assert calls == 2
        assert second != first

    run(scenario)
