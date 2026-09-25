from datetime import timedelta

from asgiref.sync import async_to_sync
from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from apps.ocpp.models import (
    Charger,
    ChargerConnection,
    InboundProtocolRequest,
    NotificationRecord,
    ProtocolOperation,
)
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.services.presence import connection_is_live
from apps.ocpp.transport.dispatch import FrameDispatcher
from apps.ocpp.transport.operations import active_connections
from tests.apps.ocpp.builders import charger, connection, protocol_operation
from tests.integration.ocpp.support import connect_charger


class TransientStateLossAcceptanceTests(TestCase):
    def test_inbound_replay_survives_loss_of_correlation_state(self) -> None:
        selected = charger("transient-replay")
        frame = Call(
            unique_id="transfer-transient",
            action="DataTransfer",
            payload={"vendorId": "ACME", "data": "durable"},
        )
        first_dispatcher = FrameDispatcher(
            charger=selected,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(selected).resolve,
        )
        first = async_to_sync(first_dispatcher.dispatch)(frame)

        fresh_dispatcher = FrameDispatcher(
            charger=selected,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(selected).resolve,
        )
        replayed = async_to_sync(fresh_dispatcher.dispatch)(frame)

        self.assertEqual(
            first,
            CallResult(
                unique_id="transfer-transient",
                payload={"status": "Accepted"},
            ),
        )
        self.assertEqual(replayed, first)
        self.assertEqual(NotificationRecord.objects.count(), 1)
        request = InboundProtocolRequest.objects.get(
            charger=selected,
            unique_id="transfer-transient",
        )
        self.assertEqual(request.status, InboundProtocolRequest.Status.COMPLETED)

    def test_pending_outbound_operation_recovers_after_live_sender_state_is_lost(self) -> None:
        selected = charger(
            "charger-1",
            connection_token_hash=make_password("charger-secret"),
        )
        operation = protocol_operation(selected, "Reset")

        active_connections.unregister(selected)

        async_to_sync(self._complete_recovered_reset)(operation.pk)

        operation.refresh_from_db()
        self.assertEqual(operation.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(operation.attempt_count, 1)
        self.assertEqual(operation.response_payload, {"status": "Accepted"})

    def test_stale_sql_presence_becomes_offline_without_transient_sender_state(self) -> None:
        selected = charger("transient-presence")
        live = connection(selected, channel_name="lost-channel")
        ChargerConnection.objects.filter(pk=live.pk).update(
            last_seen_at=timezone.now() - timedelta(minutes=20),
            lease_expires_at=timezone.now() - timedelta(minutes=10),
        )

        active_connections.unregister(selected)
        selected = Charger.objects.select_related("connection").get(pk=selected.pk)

        self.assertFalse(connection_is_live(selected))
        self.assertFalse(Charger.objects.connected().filter(pk=selected.pk).exists())
        self.assertTrue(Charger.objects.disconnected().filter(pk=selected.pk).exists())

    async def _complete_recovered_reset(self, operation_id: int) -> None:
        communicator = await connect_charger()

        outbound = await communicator.receive_json_from(timeout=5)
        self.assertEqual(outbound[0], 2)
        self.assertEqual(outbound[2], "Reset")
        self.assertEqual(outbound[3], {})
        operation = await ProtocolOperation.objects.aget(pk=operation_id)
        self.assertEqual(outbound[1], str(operation.unique_id))

        await communicator.send_json_to(
            [3, outbound[1], {"status": "Accepted"}]
        )
        await communicator.receive_nothing(timeout=0.05)
        await communicator.disconnect()
