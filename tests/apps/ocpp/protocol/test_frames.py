import pytest

from apps.ocpp.protocol.errors import ProtocolFrameError
from apps.ocpp.protocol.frames import Call, CallError, CallResult, parse_frame


def test_parse_call_result_and_error_frames() -> None:
    assert parse_frame([2, "call-1", "Heartbeat", {}]) == Call(
        unique_id="call-1",
        action="Heartbeat",
        payload={},
    )
    assert parse_frame([3, "call-1", {"currentTime": "now"}]) == CallResult(
        unique_id="call-1",
        payload={"currentTime": "now"},
    )
    assert parse_frame([4, "call-1", "NotSupported", "No", {}]) == CallError(
        unique_id="call-1",
        code="NotSupported",
        description="No",
        details={},
    )


def test_rejects_malformed_frames() -> None:
    with pytest.raises(ProtocolFrameError):
        parse_frame([2, "call-1", "Heartbeat"])
