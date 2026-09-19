from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.ocpp.domain.operations import create_operation
from apps.ocpp.models import Charger, ChargerConnection, ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.errors import (
    ConnectionClosed,
    OutboundCallError,
    OutboundCallTimeout,
)
from apps.ocpp.transport.operations import (
    ExplicitDeliveryUnavailable,
    ProtocolVersionMismatch,
    active_connections,
    deliver_queued_operation,
    register_connection,
    request_explicit_operation,
    unregister_connection,
)


class SuccessfulSender:
    version = ProtocolVersion.OCPP_16

    async def send(
        self,
        *,
        action: str,
        payload: dict[str, object],
        timeout: float = 30,
        unique_id: str | None = None,
    ) -> dict[str, object]:
        return {"status": "Accepted"}


class OutcomeSender:
    def __init__(self, outcome: Exception) -> None:
        self.outcome = outcome

    async def send(
        self,
        *,
        action: str,
        payload: dict[str, object],
        timeout: float = 30,
        unique_id: str | None = None,
    ) -> dict[str, object]:
        raise self.outcome


class DeliveryBoundaryTests(TestCase):
    def setUp(self) -> None:
        self.charger = Charger.objects.create(identity="charger-1")

    def tearDown(self) -> None:
        active_connections.unregister(self.charger)

    def test_connection_presence_is_registered_and_cleared_by_channel_owner(
        self,
    ) -> None:
        sender = SuccessfulSender()

        async_to_sync(register_connection)(
            charger=self.charger,
            sender=sender,
            channel_name="specific.channel",
            version=ProtocolVersion.OCPP_16,
        )

        connection = ChargerConnection.objects.get(charger=self.charger)
        self.assertEqual(connection.protocol, "ocpp1.6")
        self.charger.refresh_from_db()
        self.assertIsNotNone(self.charger.connected_at)

        async_to_sync(unregister_connection)(
            charger=self.charger,
            channel_name="different.channel",
        )
        self.assertTrue(ChargerConnection.objects.filter(charger=self.charger).exists())

        async_to_sync(unregister_connection)(
            charger=self.charger,
            channel_name="specific.channel",
        )
        self.assertFalse(
            ChargerConnection.objects.filter(charger=self.charger).exists()
        )
        self.charger.refresh_from_db()
        self.assertIsNone(self.charger.connected_at)

    def test_explicit_delivery_fails_closed_without_a_shared_channel_layer(
        self,
    ) -> None:
        ChargerConnection.objects.create(
            charger=self.charger,
            channel_name="specific.channel",
            protocol="ocpp1.6",
        )

        with self.assertRaises(ExplicitDeliveryUnavailable):
            async_to_sync(request_explicit_operation)(
                charger=self.charger,
                version=ProtocolVersion.OCPP_16,
                action="GetConfiguration",
                payload={},
            )

        self.assertFalse(ProtocolOperation.objects.exists())

    def test_explicit_delivery_rejects_a_live_protocol_mismatch(self) -> None:
        ChargerConnection.objects.create(
            charger=self.charger,
            channel_name="specific.channel",
            protocol="ocpp1.6",
        )

        with self.assertRaises(ProtocolVersionMismatch):
            async_to_sync(request_explicit_operation)(
                charger=self.charger,
                version=ProtocolVersion.OCPP_201,
                action="GetVariables",
                payload={"getVariableData": []},
            )

        self.assertFalse(ProtocolOperation.objects.exists())

    def test_consumer_delivery_settles_the_existing_operation(self) -> None:
        operation = create_operation(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="GetConfiguration",
            request_payload={},
        )

        async_to_sync(deliver_queued_operation)(
            charger=self.charger,
            sender=SuccessfulSender(),
            version=ProtocolVersion.OCPP_16,
            operation_id=operation.pk,
            timeout=30,
        )

        operation.refresh_from_db()
        self.assertEqual(operation.status, ProtocolOperation.Status.COMPLETED)

    def test_initial_controls_record_call_error_timeout_and_disconnect_outcomes(
        self,
    ) -> None:
        outcomes = (
            (
                "RemoteStartTransaction",
                OutcomeSender(OutboundCallError("NotSupported", "Rejected")),
                ProtocolOperation.Status.ERRORED,
            ),
            (
                "RemoteStopTransaction",
                OutcomeSender(OutboundCallTimeout("Timed out")),
                ProtocolOperation.Status.TIMED_OUT,
            ),
            (
                "Reset",
                OutcomeSender(ConnectionClosed("Disconnected")),
                ProtocolOperation.Status.DISCONNECTED,
            ),
        )
        for action, sender, expected_status in outcomes:
            operation = create_operation(
                charger=self.charger,
                version=ProtocolVersion.OCPP_16,
                direction=Direction.CSMS_TO_CHARGE_POINT,
                action=action,
                request_payload={},
            )

            async_to_sync(deliver_queued_operation)(
                charger=self.charger,
                sender=sender,
                version=ProtocolVersion.OCPP_16,
                operation_id=operation.pk,
                timeout=30,
            )

            operation.refresh_from_db()
            self.assertEqual(operation.status, expected_status)
