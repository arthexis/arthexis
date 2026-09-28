import pytest
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator

from apps.ocpp.models import Charger, CompatibilityEvidence
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

    @pytest.mark.parametrize(
        ("identity", "subprotocols"),
        [
            ("quirk-protocol-unknown", ["vendor-ocpp"]),
            ("quirk-protocol-missing", []),
        ],
    )
    def test_missing_or_unknown_subprotocol_falls_back_without_echo_and_survives(
        self,
        identity: str,
        subprotocols: list[str],
    ) -> None:
        async_to_sync(self._fallback_protocol_flow)(identity, subprotocols)

        evidence = CompatibilityEvidence.objects.get(
            charger_identity=identity,
            kind="protocol_fallback",
        )
        assert evidence.protocol == "ocpp1.6"
        assert evidence.details["offered_subprotocols"] == subprotocols

    async def _fallback_protocol_flow(
        self,
        identity: str,
        subprotocols: list[str],
    ) -> None:
        communicator = await _connect(
            identity=identity,
            subprotocols=subprotocols,
        )
        await _heartbeat(communicator, f"heartbeat-after-{identity}")
        await communicator.disconnect()

    @pytest.mark.parametrize(
        ("identity", "frame", "frame_type"),
        [
            (
                "quirk-orphan-error",
                [4, "orphan-error", "VendorError", "unexpected", {"vendor": "ACME"}],
                "CallError",
            ),
            (
                "quirk-orphan-result",
                [3, "orphan-result", {"vendor": "ACME"}],
                "CallResult",
            ),
        ],
    )
    def test_unmatched_response_is_evidence_and_does_not_break_connection(
        self,
        identity: str,
        frame: list[object],
        frame_type: str,
    ) -> None:
        async_to_sync(self._unmatched_response_flow)(identity, frame)

        evidence = CompatibilityEvidence.objects.get(
            charger_identity=identity,
            kind="unmatched_response",
        )
        assert evidence.unique_id == frame[1]
        assert evidence.details["frame_type"] == frame_type

    async def _unmatched_response_flow(
        self,
        identity: str,
        frame: list[object],
    ) -> None:
        communicator = await _connect(
            identity=identity,
            subprotocols=["ocpp1.6"],
        )
        await communicator.send_json_to(frame)
        await _heartbeat(communicator, f"heartbeat-after-{identity}")
        await communicator.disconnect()



class OcppStrictProtocolTests:
    @pytest.mark.parametrize(
        ("identity", "subprotocols"),
        [
            ("strict-protocol-unknown", ["vendor-ocpp"]),
            ("strict-protocol-missing", []),
        ],
    )
    def test_strict_charger_rejects_missing_or_unknown_subprotocol(
        self,
        identity: str,
        subprotocols: list[str],
    ) -> None:
        Charger.objects.create(
            identity=identity,
            protocol_mode=Charger.AuthorizationMode.RESTRICTED,
            authorization_mode=Charger.AuthorizationMode.RESTRICTED,
        )

        async_to_sync(self._rejected_flow)(identity, subprotocols)

        evidence = CompatibilityEvidence.objects.get(
            charger_identity=identity,
            kind="protocol_rejected",
        )
        assert evidence.details["offered_subprotocols"] == subprotocols

    async def _rejected_flow(
        self,
        identity: str,
        subprotocols: list[str],
    ) -> None:
        communicator = WebsocketCommunicator(
            application,
            f"/ws/ocpp/{identity}/",
            subprotocols=subprotocols,
        )
        connected, _ = await communicator.connect(timeout=5)
        assert connected is False
