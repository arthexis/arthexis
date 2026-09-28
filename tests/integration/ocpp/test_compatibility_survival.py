import pytest
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator

from apps.ocpp.models import CompatibilityEvidence
from arthexis.asgi import application

pytestmark = pytest.mark.django_db(transaction=True)


async def _connect(
    *,
    identity: str,
    subprotocols: list[str],
) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(
        application,
        f"/ws/ocpp/{identity}/",
        subprotocols=subprotocols,
    )
    connected, negotiated = await communicator.connect(timeout=5)
    assert connected is True
    if "ocpp1.6" in subprotocols:
        assert negotiated == "ocpp1.6"
    else:
        assert negotiated is None
    return communicator


async def _heartbeat(communicator, unique_id: str) -> None:
    await communicator.send_json_to([2, unique_id, "Heartbeat", {}])
    response = await communicator.receive_json_from()
    assert response[0:2] == [3, unique_id]
    assert "currentTime" in response[2]


class OcppCompatibilitySurvivalTests:
    def test_unsupported_action_returns_error_but_connection_survives(self) -> None:
        async_to_sync(self._unsupported_action_flow)()

        evidence = CompatibilityEvidence.objects.get(
            charger_identity="quirk-unsupported",
            kind="unsupported_action",
        )
        assert evidence.action == "VendorMagic"
        assert evidence.unique_id == "vendor-1"

    async def _unsupported_action_flow(self) -> None:
        communicator = await _connect(
            identity="quirk-unsupported",
            subprotocols=["ocpp1.6"],
        )
        await communicator.send_json_to(
            [2, "vendor-1", "VendorMagic", {"vendor": "ACME"}]
        )
        response = await communicator.receive_json_from()
        assert response[0:3] == [4, "vendor-1", "NotSupported"]

        await _heartbeat(communicator, "heartbeat-after-vendor")
        await communicator.disconnect()

    def test_malformed_call_returns_call_error_but_connection_survives(self) -> None:
        async_to_sync(self._malformed_frame_flow)()

        evidence = CompatibilityEvidence.objects.get(
            charger_identity="quirk-malformed",
            kind="malformed_frame",
        )
        assert evidence.unique_id == "bad-1"

    async def _malformed_frame_flow(self) -> None:
        communicator = await _connect(
            identity="quirk-malformed",
            subprotocols=["ocpp1.6"],
        )
        await communicator.send_json_to([2, "bad-1"])
        response = await communicator.receive_json_from()
        assert response[0:2] == [4, "bad-1"]

        await _heartbeat(communicator, "heartbeat-after-malformed")
        await communicator.disconnect()

    def test_unknown_subprotocol_falls_back_without_echo_and_remains_operational(
        self,
    ) -> None:
        async_to_sync(self._unknown_subprotocol_flow)()

        evidence = CompatibilityEvidence.objects.get(
            charger_identity="quirk-protocol",
            kind="protocol_fallback",
        )
        assert evidence.protocol == "ocpp1.6"
        assert evidence.details["offered_subprotocols"] == ["vendor-ocpp"]

    async def _unknown_subprotocol_flow(self) -> None:
        communicator = await _connect(
            identity="quirk-protocol",
            subprotocols=["vendor-ocpp"],
        )
        await _heartbeat(communicator, "heartbeat-after-fallback")
        await communicator.disconnect()

    def test_unmatched_response_is_evidence_and_does_not_break_connection(
        self,
    ) -> None:
        async_to_sync(self._unmatched_response_flow)()

        evidence = CompatibilityEvidence.objects.get(
            charger_identity="quirk-orphan",
            kind="unmatched_response",
        )
        assert evidence.unique_id == "orphan-1"
        assert evidence.details["frame_type"] == "CallError"

    async def _unmatched_response_flow(self) -> None:
        communicator = await _connect(
            identity="quirk-orphan",
            subprotocols=["ocpp1.6"],
        )
        await communicator.send_json_to(
            [4, "orphan-1", "VendorError", "unexpected", {"vendor": "ACME"}]
        )
        await _heartbeat(communicator, "heartbeat-after-orphan")
        await communicator.disconnect()
