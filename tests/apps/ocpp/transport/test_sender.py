import asyncio
from unittest import IsolatedAsyncioTestCase

from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.errors import OutboundCallError, OutboundCallTimeout
from apps.ocpp.protocol.frames import CallError, CallResult
from apps.ocpp.transport.sender import OutboundSender


class OutboundSenderTests(IsolatedAsyncioTestCase):
    async def test_outbound_result_is_correlated(self) -> None:
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
        self.assertIsInstance(unique_id, str)
        pending_calls.resolve(
            CallResult(unique_id=unique_id, payload={"configurationKey": []})
        )

        self.assertEqual(await task, {"configurationKey": []})

    async def test_outbound_timeout_is_bounded(self) -> None:
        async def send_json(frame: list[object]) -> None:
            return None

        pending_calls = PendingCalls()
        sender = OutboundSender(
            version=ProtocolVersion.OCPP_16,
            send_json=send_json,
            pending_calls=pending_calls,
        )
        with self.assertRaises(OutboundCallTimeout):
            await sender.send(action="GetConfiguration", payload={}, timeout=0)

    async def test_outbound_call_error_is_correlated(self) -> None:
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

        with self.assertRaises(OutboundCallError):
            await task
