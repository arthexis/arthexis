from datetime import datetime, timedelta, timezone

from django.test import TestCase

from apps.ocpp.domain.operations import (
    create_operation,
    reconcile_reservation_operation,
)
from apps.ocpp.domain.reservations import record_reservation
from apps.ocpp.models import Connector, ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from tests.apps.ocpp.builders import charger


class ReservationOperationReconciliationTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("reconcile-reservation")
        self.attempt_at = datetime(2026, 9, 24, 20, tzinfo=timezone.utc)

    def _ambiguous(
        self,
        *,
        version: ProtocolVersion,
        action: str,
        payload: dict[str, object],
    ) -> ProtocolOperation:
        operation = create_operation(
            charger=self.charger,
            version=version,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action=action,
            request_payload=payload,
        )
        operation.status = ProtocolOperation.Status.RECOVERY_REQUIRED
        operation.attempt_count = 1
        operation.first_attempt_at = self.attempt_at
        operation.last_attempt_at = self.attempt_at
        operation.save(
            update_fields=(
                "status",
                "attempt_count",
                "first_attempt_at",
                "last_attempt_at",
            )
        )
        return operation

    def _reservation(
        self,
        *,
        remote_id: str,
        id_tag: str = "card-1",
        status: str = "pending",
        connector_number: int | None = None,
        updated_at: datetime | None = None,
    ):
        connector = None
        if connector_number is not None:
            connector = Connector.objects.create(
                charger=self.charger,
                number=connector_number,
            )
        reservation = record_reservation(
            charger=self.charger,
            remote_id=remote_id,
            id_tag=id_tag,
            expires_at=self.attempt_at + timedelta(hours=1),
            connector=connector,
            status=status,
        )
        if updated_at is not None:
            reservation.__class__.objects.filter(pk=reservation.pk).update(
                updated_at=updated_at
            )
            reservation.refresh_from_db()
        return reservation

    def test_v16_fresh_matching_reservation_proves_reserve_now_achieved(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ReserveNow",
            payload={
                "connectorId": 1,
                "expiryDate": "2026-09-24T21:00:00Z",
                "idTag": "card-1",
                "reservationId": 41,
            },
        )
        reservation = self._reservation(
            remote_id="41",
            connector_number=1,
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_reservation_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertIn(reservation.remote_id, result.reconciliation_basis)
        self.assertEqual(
            ProtocolOperation.objects.filter(action="ReserveNow").count(),
            1,
        )

    def test_v16_terminal_reservation_creates_deliberate_reserve_replacement(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ReserveNow",
            payload={
                "connectorId": 1,
                "expiryDate": "2026-09-24T21:00:00Z",
                "idTag": "card-1",
                "reservationId": 42,
            },
        )
        self._reservation(
            remote_id="42",
            status="rejected",
            connector_number=1,
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_reservation_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED,
        )
        replacement = (
            ProtocolOperation.objects.filter(action="ReserveNow")
            .exclude(pk=operation.pk)
            .get()
        )
        self.assertEqual(replacement.request_payload, operation.request_payload)
        self.assertEqual(replacement.status, ProtocolOperation.Status.PENDING)

    def test_fresh_terminal_state_proves_cancel_reservation_achieved(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="CancelReservation",
            payload={"reservationId": 43},
        )
        self._reservation(
            remote_id="43",
            status="cancelled",
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_reservation_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertEqual(
            ProtocolOperation.objects.filter(action="CancelReservation").count(),
            1,
        )

    def test_fresh_active_state_creates_deliberate_cancel_replacement(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="CancelReservation",
            payload={"reservationId": 44},
        )
        self._reservation(
            remote_id="44",
            status="accepted",
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_reservation_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED,
        )
        replacement = (
            ProtocolOperation.objects.filter(action="CancelReservation")
            .exclude(pk=operation.pk)
            .get()
        )
        self.assertEqual(replacement.status, ProtocolOperation.Status.PENDING)

    def test_stale_reservation_state_does_not_resolve_ambiguity(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="CancelReservation",
            payload={"reservationId": 45},
        )
        self._reservation(
            remote_id="45",
            status="cancelled",
            updated_at=self.attempt_at - timedelta(seconds=1),
        )

        result = reconcile_reservation_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.RECOVERY_REQUIRED)
        self.assertIsNone(result.reconciled_at)

    def test_v201_reservation_identity_uses_id_token_and_evse(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            action="ReserveNow",
            payload={
                "id": 46,
                "reservationId": 46,
                "expiryDateTime": "2026-09-24T21:00:00Z",
                "idToken": {"idToken": "card-2", "type": "Central"},
                "evseId": 2,
            },
        )
        self._reservation(
            remote_id="46",
            id_tag="card-2",
            connector_number=2001,
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_reservation_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )

    def test_repeated_sweep_does_not_create_duplicate_replacement(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="CancelReservation",
            payload={"reservationId": 47},
        )
        self._reservation(
            remote_id="47",
            status="active",
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        first = reconcile_reservation_operation(operation)
        second = reconcile_reservation_operation(operation)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            ProtocolOperation.objects.filter(action="CancelReservation").count(),
            2,
        )
