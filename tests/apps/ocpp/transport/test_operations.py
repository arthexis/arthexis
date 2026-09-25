from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
import pytest
from channels.exceptions import ChannelFull
from django.utils import timezone

from apps.ocpp.domain.operations import (
    claim_operation,
    create_operation,
    prepare_reconnect_operations,
    require_operation_recovery,
    settle_operation_attempt,
)
from apps.ocpp.models import ChargerConnection, ProtocolOperation
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
    recover_connected_operations,
    register_connection,
    request_explicit_operation,
    unregister_connection,
)
from tests.apps.ocpp.builders import charger, connection

pytestmark = pytest.mark.django_db


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


class CountingSender(SuccessfulSender):
    def __init__(self) -> None:
        self.calls = 0

    async def send(
        self,
        *,
        action: str,
        payload: dict[str, object],
        timeout: float = 30,
        unique_id: str | None = None,
    ) -> dict[str, object]:
        self.calls += 1
        return await super().send(
            action=action,
            payload=payload,
            timeout=timeout,
            unique_id=unique_id,
        )


class SharedLayer:
    def __init__(self, outcome: Exception | None = None) -> None:
        self.outcome = outcome
        self.messages: list[tuple[str, dict[str, object]]] = []

    async def send(self, channel: str, message: dict[str, object]) -> None:
        if self.outcome is not None:
            raise self.outcome
        self.messages.append((channel, message))


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


class DeliveryBoundaryTests:
    def setup_method(self) -> None:
        self.charger = charger("charger-1")

    def teardown_method(self) -> None:
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
        assert connection.protocol == "ocpp1.6"
        self.charger.refresh_from_db()
        assert self.charger.connected_at is not None

        async_to_sync(unregister_connection)(
            charger=self.charger,
            channel_name="different.channel",
        )
        assert ChargerConnection.objects.filter(charger=self.charger).exists()

        async_to_sync(unregister_connection)(
            charger=self.charger,
            channel_name="specific.channel",
        )
        assert not ChargerConnection.objects.filter(charger=self.charger).exists()
        self.charger.refresh_from_db()
        assert self.charger.connected_at is None

    def test_explicit_delivery_fails_closed_without_a_shared_channel_layer(
        self,
    ) -> None:
        connection(self.charger, channel_name="specific.channel")

        with pytest.raises(ExplicitDeliveryUnavailable):
            async_to_sync(request_explicit_operation)(
                charger=self.charger,
                version=ProtocolVersion.OCPP_16,
                action="GetConfiguration",
                payload={},
            )

        operation = ProtocolOperation.objects.get()
        assert operation.status == ProtocolOperation.Status.PENDING
        assert operation.attempt_count == 0
        assert "shared channel layer" in operation.last_delivery_error

    def test_explicit_delivery_rejects_expired_persisted_connection(self) -> None:
        live = connection(self.charger, channel_name="specific.channel")
        ChargerConnection.objects.filter(pk=live.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )

        with pytest.raises(ExplicitDeliveryUnavailable):
            async_to_sync(request_explicit_operation)(
                charger=self.charger,
                version=ProtocolVersion.OCPP_16,
                action="GetConfiguration",
                payload={},
            )

        operation = ProtocolOperation.objects.get()
        assert operation.status == ProtocolOperation.Status.PENDING
        assert operation.attempt_count == 0
        assert "not connected" in operation.last_delivery_error

    def test_explicit_delivery_rejects_a_live_protocol_mismatch(self) -> None:
        connection(self.charger, channel_name="specific.channel")

        with pytest.raises(ProtocolVersionMismatch):
            async_to_sync(request_explicit_operation)(
                charger=self.charger,
                version=ProtocolVersion.OCPP_201,
                action="GetVariables",
                payload={"getVariableData": []},
            )

        operation = ProtocolOperation.objects.get()
        assert operation.status == ProtocolOperation.Status.ERRORED
        assert operation.error_code == "ProtocolVersionMismatch"
        assert operation.attempt_count == 0
        assert operation.completed_at is not None

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
        assert operation.status == ProtocolOperation.Status.COMPLETED
        assert operation.attempt_count == 1
        assert operation.first_attempt_at is not None
        assert operation.first_attempt_at == operation.last_attempt_at

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
                ProtocolOperation.Status.RECOVERY_REQUIRED,
            ),
            (
                "Reset",
                OutcomeSender(ConnectionClosed("Disconnected")),
                ProtocolOperation.Status.RECOVERY_REQUIRED,
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
            assert operation.status == expected_status
            assert operation.attempt_count == 1
            assert operation.first_attempt_at is not None
            assert operation.last_attempt_at is not None
            if expected_status == ProtocolOperation.Status.RECOVERY_REQUIRED:
                assert operation.completed_at is None
                assert operation.last_delivery_error


    def test_channel_enqueue_failure_preserves_pending_intent(self) -> None:
        connection(self.charger, channel_name="specific.channel")
        layer = SharedLayer(ChannelFull())

        with patch(
            "apps.ocpp.transport.operations.get_channel_layer",
            return_value=layer,
        ):
            with pytest.raises(ExplicitDeliveryUnavailable):
                async_to_sync(request_explicit_operation)(
                    charger=self.charger,
                    version=ProtocolVersion.OCPP_16,
                    action="GetConfiguration",
                    payload={},
                )

        operation = ProtocolOperation.objects.get()
        assert operation.status == ProtocolOperation.Status.PENDING
        assert operation.attempt_count == 0
        assert operation.first_attempt_at is None
        assert "Could not confirm delivery" in operation.last_delivery_error

    def test_successful_channel_enqueue_remains_pending_until_consumer_claims(self) -> None:
        connection(self.charger, channel_name="specific.channel")
        layer = SharedLayer()

        with patch(
            "apps.ocpp.transport.operations.get_channel_layer",
            return_value=layer,
        ):
            operation = async_to_sync(request_explicit_operation)(
                charger=self.charger,
                version=ProtocolVersion.OCPP_16,
                action="GetConfiguration",
                payload={},
            )

        operation.refresh_from_db()
        assert operation.status == ProtocolOperation.Status.PENDING
        assert operation.attempt_count == 0
        assert len(layer.messages) == 1
        _, message = layer.messages[0]
        assert message["operation_id"] == operation.pk

    def test_duplicate_queue_events_cannot_send_same_pending_operation_twice(self) -> None:
        operation = create_operation(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="GetConfiguration",
            request_payload={},
        )
        sender = CountingSender()

        async_to_sync(deliver_queued_operation)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
            operation_id=operation.pk,
            timeout=30,
        )
        async_to_sync(deliver_queued_operation)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
            operation_id=operation.pk,
            timeout=30,
        )

        operation.refresh_from_db()
        assert sender.calls == 1
        assert operation.attempt_count == 1
        assert operation.status == ProtocolOperation.Status.COMPLETED

    def test_timeout_preserves_ambiguous_send_for_recovery(self) -> None:
        operation = create_operation(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="GetConfiguration",
            request_payload={},
        )

        async_to_sync(deliver_queued_operation)(
            charger=self.charger,
            sender=OutcomeSender(OutboundCallTimeout("response lost")),
            version=ProtocolVersion.OCPP_16,
            operation_id=operation.pk,
            timeout=30,
        )

        operation.refresh_from_db()
        assert operation.status == ProtocolOperation.Status.RECOVERY_REQUIRED,
        assert operation.attempt_count == 1
        assert operation.completed_at is None
        assert operation.last_delivery_error == "response lost"

        sender = CountingSender()
        async_to_sync(deliver_queued_operation)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
            operation_id=operation.pk,
            timeout=30,
        )
        assert sender.calls == 0



class ReconnectOutboundRecoveryTests:
    def setup_method(self) -> None:
        self.charger = charger("reconnect-recovery")

    def _operation(
        self,
        *,
        action: str,
        status: str = ProtocolOperation.Status.PENDING,
    ) -> ProtocolOperation:
        operation = create_operation(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action=action,
            request_payload={},
        )
        if status != ProtocolOperation.Status.PENDING:
            operation.status = status
            operation.save(update_fields=("status",))
        return operation

    def test_reconnect_delivers_known_unsent_pending_work(self) -> None:
        operation = self._operation(action="Reset")
        sender = CountingSender()

        async_to_sync(recover_connected_operations)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
        )

        operation.refresh_from_db()
        assert sender.calls == 1
        assert operation.attempt_count == 1
        assert operation.status == ProtocolOperation.Status.COMPLETED

    def test_reconnect_retries_safe_ambiguous_query(self) -> None:
        operation = self._operation(
            action="GetConfiguration",
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
        )
        operation.attempt_count = 1
        operation.save(update_fields=("attempt_count",))
        sender = CountingSender()

        async_to_sync(recover_connected_operations)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
        )

        operation.refresh_from_db()
        assert sender.calls == 1
        assert operation.attempt_count == 2
        assert operation.status == ProtocolOperation.Status.COMPLETED

    def test_reconnect_does_not_retry_reconcile_or_manual_ambiguity(self) -> None:
        reconcile = self._operation(
            action="RemoteStopTransaction",
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
        )
        manual = self._operation(
            action="Reset",
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
        )
        sender = CountingSender()

        async_to_sync(recover_connected_operations)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
        )

        assert sender.calls == 0
        reconcile.refresh_from_db()
        manual.refresh_from_db()
        assert reconcile.status == ProtocolOperation.Status.RECOVERY_REQUIRED,
        assert manual.status == ProtocolOperation.Status.RECOVERY_REQUIRED,

    def test_interrupted_delivery_becomes_ambiguous_then_safe_retry_only(self) -> None:
        safe = self._operation(
            action="GetConfiguration",
            status=ProtocolOperation.Status.DELIVERING,
        )
        reconcile = self._operation(
            action="RemoteStopTransaction",
            status=ProtocolOperation.Status.DELIVERING,
        )
        safe.attempt_count = 1
        safe.delivery_owner = "old.channel"
        reconcile.attempt_count = 1
        reconcile.delivery_owner = "old.channel"
        safe.save(update_fields=("attempt_count", "delivery_owner"))
        reconcile.save(update_fields=("attempt_count", "delivery_owner"))
        sender = CountingSender()

        async_to_sync(recover_connected_operations)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
            delivery_owner="new.channel",
        )

        safe.refresh_from_db()
        reconcile.refresh_from_db()
        assert sender.calls == 1
        assert safe.status == ProtocolOperation.Status.COMPLETED
        assert safe.attempt_count == 2
        assert reconcile.status == ProtocolOperation.Status.RECOVERY_REQUIRED,
        assert reconcile.attempt_count == 1
        assert "Previous consumer ended" in reconcile.last_delivery_error

    def test_repeated_reconnect_does_not_repeat_completed_recovery(self) -> None:
        operation = self._operation(
            action="GetConfiguration",
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
        )
        operation.attempt_count = 1
        operation.save(update_fields=("attempt_count",))
        sender = CountingSender()

        async_to_sync(recover_connected_operations)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
        )
        async_to_sync(recover_connected_operations)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
        )

        operation.refresh_from_db()
        assert sender.calls == 1
        assert operation.attempt_count == 2
        assert operation.status == ProtocolOperation.Status.COMPLETED

    def test_reconnect_recovers_only_matching_protocol_version(self) -> None:
        operation = self._operation(action="GetConfiguration")
        sender = CountingSender()

        async_to_sync(recover_connected_operations)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_201,
        )

        operation.refresh_from_db()
        assert sender.calls == 0
        assert operation.status == ProtocolOperation.Status.PENDING
        assert operation.attempt_count == 0



class OutboundRaceHardeningTests:
    def setup_method(self) -> None:
        self.charger = charger("race-hardening")

    def teardown_method(self) -> None:
        active_connections.unregister(self.charger)

    def test_old_disconnect_does_not_unregister_new_connection_sender(self) -> None:
        old_sender = CountingSender()
        new_sender = CountingSender()

        async_to_sync(register_connection)(
            charger=self.charger,
            sender=old_sender,
            channel_name="old.channel",
            version=ProtocolVersion.OCPP_16,
        )
        async_to_sync(register_connection)(
            charger=self.charger,
            sender=new_sender,
            channel_name="new.channel",
            version=ProtocolVersion.OCPP_16,
        )
        async_to_sync(unregister_connection)(
            charger=self.charger,
            channel_name="old.channel",
        )

        assert active_connections.get(self.charger) is new_sender
        persisted = ChargerConnection.objects.get(charger=self.charger)
        assert persisted.channel_name == "new.channel"

    def test_same_owner_recovery_does_not_abandon_its_inflight_attempt(self) -> None:
        operation = create_operation(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="GetConfiguration",
            request_payload={},
        )
        claimed = claim_operation(operation, delivery_owner="current.channel")
        assert claimed is not None
        sender = CountingSender()

        async_to_sync(recover_connected_operations)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
            delivery_owner="current.channel",
        )

        operation.refresh_from_db()
        assert sender.calls == 0
        assert operation.status == ProtocolOperation.Status.DELIVERING
        assert operation.delivery_owner == "current.channel"
        assert operation.attempt_count == 1

    def test_new_owner_supersedes_old_safe_attempt_and_late_outcome_is_ignored(
        self,
    ) -> None:
        operation = create_operation(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="GetConfiguration",
            request_payload={},
        )
        first = claim_operation(operation, delivery_owner="old.channel")
        assert first is not None
        assert first.attempt_token is not None
        first_token = first.attempt_token

        pending = prepare_reconnect_operations(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            delivery_owner="new.channel",
        )
        assert pending == (operation.pk,)

        operation.refresh_from_db()
        assert operation.status == ProtocolOperation.Status.PENDING
        second = claim_operation(operation, delivery_owner="new.channel")
        assert second is not None
        assert second.attempt_token is not None
        second_token = second.attempt_token
        assert first_token != second_token
        assert second.attempt_count == 2

        settle_operation_attempt(
            first,
            attempt_token=first_token,
            response_payload={"status": "old-success"},
        )
        require_operation_recovery(
            first,
            attempt_token=first_token,
            description="old timeout",
        )

        operation.refresh_from_db()
        assert operation.status == ProtocolOperation.Status.DELIVERING
        assert operation.attempt_token == second_token
        assert operation.delivery_owner == "new.channel"
        assert operation.response_payload is None

        settle_operation_attempt(
            second,
            attempt_token=second_token,
            response_payload={"status": "new-success"},
        )

        operation.refresh_from_db()
        assert operation.status == ProtocolOperation.Status.COMPLETED
        assert operation.response_payload == {"status": "new-success"}
        assert operation.attempt_count == 2

    def test_new_owner_does_not_resend_reconcile_attempt_after_takeover(self) -> None:
        operation = create_operation(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="RemoteStopTransaction",
            request_payload={},
        )
        first = claim_operation(operation, delivery_owner="old.channel")
        assert first is not None
        sender = CountingSender()

        async_to_sync(recover_connected_operations)(
            charger=self.charger,
            sender=sender,
            version=ProtocolVersion.OCPP_16,
            delivery_owner="new.channel",
        )

        operation.refresh_from_db()
        assert sender.calls == 0
        assert operation.status == ProtocolOperation.Status.RECOVERY_REQUIRED,
        assert operation.attempt_count == 1
        assert "Previous consumer ended" in operation.last_delivery_error
