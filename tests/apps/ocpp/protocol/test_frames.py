from unittest import TestCase

from apps.ocpp.protocol.errors import ProtocolFrameError
from apps.ocpp.protocol.frames import Call, CallError, CallResult, parse_frame


class FrameTests(TestCase):
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
