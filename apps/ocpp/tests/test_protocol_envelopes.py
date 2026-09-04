from apps.ocpp.consumers.csms.protocol import (
    OCPPCallEnvelope,
    OCPPCallErrorEnvelope,
    OCPPCallResultEnvelope,
    message_type,
    validate_call_envelope,
    validate_call_error_envelope,
    validate_call_result_envelope,
    validate_message_envelope,
)


def test_message_type_accepts_supported_discriminators():
    assert message_type([2]) == 2
    assert message_type([3]) == 3
    assert message_type([4]) == 4
    assert message_type([5]) is None
    assert message_type({"type": 2}) is None


def test_validate_call_envelope_returns_typed_payload():
    envelope = validate_call_envelope(
        [2, "call-1", "StatusNotification", {"connectorId": 1}]
    )

    assert envelope == OCPPCallEnvelope(
        message_id="call-1",
        action="StatusNotification",
        payload={"connectorId": 1},
    )
    assert validate_message_envelope(
        [2, "call-1", "StatusNotification", {"connectorId": 1}]
    ) == envelope


def test_validate_call_result_envelope_returns_typed_payload():
    envelope = validate_call_result_envelope([3, "call-2", {"status": "Accepted"}])

    assert envelope == OCPPCallResultEnvelope(
        message_id="call-2",
        payload={"status": "Accepted"},
    )
    assert validate_message_envelope([3, "call-2", {"status": "Accepted"}]) == envelope


def test_validate_call_error_envelope_returns_typed_payload():
    envelope = validate_call_error_envelope(
        [4, "call-3", "NotSupported", "Unsupported action", {"vendor": "ACME"}]
    )

    assert envelope == OCPPCallErrorEnvelope(
        message_id="call-3",
        error_code="NotSupported",
        description="Unsupported action",
        details={"vendor": "ACME"},
    )
    assert validate_message_envelope(
        [4, "call-3", "NotSupported", "Unsupported action", {"vendor": "ACME"}]
    ) == envelope


def test_malformed_response_frames_are_rejected_before_dispatch():
    assert validate_message_envelope([3, "call-4"]) is None
    assert validate_message_envelope([3, "call-4", []]) is None
    assert validate_message_envelope([4, "call-5", "InternalError", "boom"]) is None
    assert (
        validate_message_envelope(
            [4, "call-5", "InternalError", "boom", "not-an-object"]
        )
        is None
    )
