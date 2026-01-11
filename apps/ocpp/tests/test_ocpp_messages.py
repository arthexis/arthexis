import pytest

from apps.ocpp.messages import (
    MessageDecodeError,
    MessageValidationError,
    build_request,
    build_response,
    decode_call,
    encode_call,
    encode_call_result,
)


def test_decode_rejects_malformed_message():
    with pytest.raises(MessageDecodeError):
        decode_call("not-json", ocpp_version="ocpp1.6")

    with pytest.raises(MessageDecodeError):
        decode_call({"type": 2}, ocpp_version="ocpp1.6")

    with pytest.raises(MessageDecodeError):
        decode_call([3, "msg", {}], ocpp_version="ocpp1.6")


def test_decode_rejects_mismatched_version():
    message = [2, "msg-1", "BootNotification", {"chargePointVendor": "ACME", "chargePointModel": "X"}]
    with pytest.raises(MessageDecodeError):
        decode_call(message, ocpp_version="ocpp2.0.1")


def test_round_trip_encode_decode_across_versions():
    request16 = build_request(
        "BootNotification",
        ocpp_version="ocpp1.6",
        payload={"chargePointVendor": "ACME", "chargePointModel": "ModelX"},
    )
    encoded16 = encode_call(request16, message_id="msg-16")
    decoded16 = decode_call(encoded16, ocpp_version="ocpp1.6")
    assert decoded16.message_id == "msg-16"
    assert decoded16.request.payload["chargePointVendor"] == "ACME"

    request201 = build_request(
        "TransactionEvent",
        ocpp_version="ocpp2.0.1",
        payload={"eventType": "Started", "timestamp": "2024-01-01T00:00:00Z"},
    )
    encoded201 = encode_call(request201, message_id="msg-201")
    decoded201 = decode_call(encoded201, ocpp_version="ocpp2.0.1")
    assert decoded201.request.payload["eventType"] == "Started"

    request21 = build_request(
        "CostUpdated",
        ocpp_version="ocpp2.1",
        payload={"transactionId": "TX-1", "totalCost": "12.5"},
    )
    encoded21 = encode_call(request21, message_id="msg-21")
    decoded21 = decode_call(encoded21, ocpp_version="ocpp2.1")
    assert decoded21.action == "CostUpdated"


def test_response_validation_and_encoding():
    response = build_response(
        "BootNotification",
        ocpp_version="ocpp1.6",
        payload={"currentTime": "2024-01-01T00:00:00Z", "interval": 300, "status": "Accepted"},
    )
    response.message_id = "msg-resp"
    encoded = encode_call_result(response)
    assert encoded[0] == 3
    assert encoded[1] == "msg-resp"

    with pytest.raises(MessageValidationError):
        build_response("BootNotification", ocpp_version="ocpp1.6", payload={"interval": 1})
