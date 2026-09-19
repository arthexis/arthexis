import asyncio
from unittest import IsolatedAsyncioTestCase

from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.errors import (
    ConnectionClosed,
    OutboundCallError,
    OutboundCallTimeout,
    ProtocolFrameError,
)
from apps.ocpp.protocol.frames import Call, CallError, CallResult, parse_frame
from apps.ocpp.transport.connection import (
    ConnectionRejected,
    basic_credentials,
    negotiate_subprotocol,
)
from apps.ocpp.transport.sender import OutboundSender


class FrameTests(IsolatedAsyncioTestCase):
    def test_parse_call_result_and_error_frames(self) -> None:
        self.assertEqual(
            parse_frame([2, "call-1", "Heartbeat", {}]),
            Call(unique_id="call-1", action="Heartbeat", payload={}),
        )
        self.assertEqual(
            parse_frame([3, "call-1", {"currentTime": "now"}]),
            CallResult(unique_id="call-1", payload={"currentTime": "now"}),
        )
        self.assertEqual(
            parse_frame([4, "call-1", "NotSupported", "No", {}]),
            CallError(
                unique_id="call-1",
                code="NotSupported",
                description="No",
                details={},
            ),
        )

    def test_rejects_malformed_frames(self) -> None:
        with self.assertRaises(ProtocolFrameError):
            parse_frame([2, "call-1", "Heartbeat"])


class CorrelationTests(IsolatedAsyncioTestCase):
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

    async def test_outbound_timeout_and_disconnect_are_bounded(self) -> None:
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

        unique_id, future = pending_calls.open()
        pending_calls.close()
        with self.assertRaises(ConnectionClosed):
            await pending_calls.wait(unique_id, future, timeout=1)

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

    def test_negotiation_requires_an_offered_retained_version(self) -> None:
        self.assertEqual(
            negotiate_subprotocol(["ocpp2.0.1"]),
            ("ocpp2.0.1", ProtocolVersion.OCPP_201),
        )
        with self.assertRaises(ConnectionRejected):
            negotiate_subprotocol(["ocpp1.5"])

    def test_basic_credentials_are_parsed_without_retaining_headers(self) -> None:
        self.assertEqual(
            basic_credentials([(b"authorization", b"Basic Y2hhcmdlcjE6c2VjcmV0")]),
            ("charger1", "secret"),
        )
