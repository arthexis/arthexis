import asyncio

import pytest
from asgiref.sync import async_to_sync

from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.errors import OutboundCallError, OutboundCallTimeout
from apps.ocpp.protocol.frames import CallError, CallResult
from apps.ocpp.transport.sender import OutboundSender


def run(coro):
    return async_to_sync(coro)()


def test_outbound_result_is_correlated() -> None:
    async def scenario() -> None:
        sent: list[list[object]] = []

        async def send_json(frame: list[object]) -> None:
            sent.append(frame)

        pending_calls = PendingCalls()
        sender = OutboundSender(
            version=ProtocolVersion.OCPP_16,
            send_json=send_json,
            pending_calls=pending_calls,
        )
        task = asyncio.create_task(sender.send(action="GetConfiguration", payload={}))
        await asyncio.sleep(0)
        unique_id = sent[0][1]
        assert isinstance(unique_id, str)
        pending_calls.resolve(
            CallResult(unique_id=unique_id, payload={"configurationKey": []})
        )

        assert await task == {"configurationKey": []}

    run(scenario)


def test_outbound_timeout_is_bounded() -> None:
    async def scenario() -> None:
        async def send_json(frame: list[object]) -> None:
            return None

        pending_calls = PendingCalls()
        sender = OutboundSender(
            version=ProtocolVersion.OCPP_16,
            send_json=send_json,
            pending_calls=pending_calls,
        )
        with pytest.raises(OutboundCallTimeout):
            await sender.send(action="GetConfiguration", payload={}, timeout=0)

    run(scenario)


def test_outbound_call_error_is_correlated() -> None:
    async def scenario() -> None:
        sent: list[list[object]] = []

        async def send_json(frame: list[object]) -> None:
            sent.append(frame)

        pending_calls = PendingCalls()
        sender = OutboundSender(
            version=ProtocolVersion.OCPP_16,
            send_json=send_json,
            pending_calls=pending_calls,
        )
        task = asyncio.create_task(sender.send(action="GetConfiguration", payload={}))
        await asyncio.sleep(0)
        pending_calls.resolve(
            CallError(
                unique_id=sent[0][1],
                code="NotSupported",
                description="Unsupported",
                details={},
            )
        )

        with pytest.raises(OutboundCallError):
            await task

    run(scenario)
