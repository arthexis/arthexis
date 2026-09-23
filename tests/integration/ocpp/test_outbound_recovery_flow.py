from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth.hashers import make_password
from django.test import TestCase

from apps.ocpp.domain.operations import create_operation
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from arthexis.asgi import application
from tests.apps.ocpp.builders import charger
from tests.integration.ocpp.support import basic_authorization


class OutboundRecoveryAcceptanceTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger(
            "charger-1",
            connection_token_hash=make_password("charger-secret"),
        )

    def _operation(
        self,
        *,
        action: str,
        status: str = ProtocolOperation.Status.PENDING,
        attempts: int = 0,
    ) -> ProtocolOperation:
        operation = create_operation(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action=action,
            request_payload={},
        )
        operation.status = status
        operation.attempt_count = attempts
        operation.save(update_fields=("status", "attempt_count"))
        return operation

    def test_restart_before_send_delivers_durable_pending_intent(self) -> None:
        operation = self._operation(action="Reset")

        async_to_sync(self._complete_recovered_call)(
            operation=operation,
            expected_action="Reset",
            response={"status": "Accepted"},
        )

        operation.refresh_from_db()
        self.assertEqual(operation.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(operation.attempt_count, 1)
        self.assertEqual(operation.response_payload, {"status": "Accepted"})
        self.assertTrue(operation.delivery_owner)
        self.assertIsNotNone(operation.attempt_token)

    def test_restart_after_possible_send_retries_safe_query(self) -> None:
        operation = self._operation(
            action="GetConfiguration",
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
            attempts=1,
        )

        async_to_sync(self._complete_recovered_call)(
            operation=operation,
            expected_action="GetConfiguration",
            response={"configurationKey": [], "unknownKey": []},
        )

        operation.refresh_from_db()
        self.assertEqual(operation.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(operation.attempt_count, 2)
        self.assertEqual(
            operation.response_payload,
            {"configurationKey": [], "unknownKey": []},
        )

    def test_restart_after_ambiguous_unsafe_command_does_not_resend(self) -> None:
        operation = self._operation(
            action="Reset",
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
            attempts=1,
        )

        async_to_sync(self._connect_and_assert_no_outbound_call)()

        operation.refresh_from_db()
        self.assertEqual(
            operation.status,
            ProtocolOperation.Status.RECOVERY_REQUIRED,
        )
        self.assertEqual(operation.attempt_count, 1)
        self.assertEqual(
            operation.recovery_policy,
            ProtocolOperation.RecoveryPolicy.MANUAL,
        )
        self.assertIsNone(operation.completed_at)

    async def _connect(self) -> WebsocketCommunicator:
        communicator = WebsocketCommunicator(
            application,
            "/ws/ocpp/charger-1/",
            subprotocols=["ocpp1.6"],
            headers=[(b"authorization", basic_authorization())],
        )
        connected, subprotocol = await communicator.connect(timeout=5)
        self.assertTrue(connected)
        self.assertEqual(subprotocol, "ocpp1.6")
        return communicator

    async def _complete_recovered_call(
        self,
        *,
        operation: ProtocolOperation,
        expected_action: str,
        response: dict[str, object],
    ) -> None:
        communicator = await self._connect()
        outbound = await communicator.receive_json_from(timeout=5)

        self.assertEqual(outbound[0], 2)
        self.assertEqual(outbound[2], expected_action)
        self.assertEqual(outbound[3], {})
        unique_id = outbound[1]
        self.assertEqual(unique_id, str(operation.unique_id))

        await communicator.send_json_to([3, unique_id, response])
        await communicator.receive_nothing(timeout=0.05)
        await communicator.disconnect()

    async def _connect_and_assert_no_outbound_call(self) -> None:
        communicator = await self._connect()
        self.assertTrue(await communicator.receive_nothing(timeout=0.1))
        await communicator.disconnect()
