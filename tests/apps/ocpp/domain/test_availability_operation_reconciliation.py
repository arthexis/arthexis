from datetime import datetime, timedelta, timezone

import pytest

from apps.ocpp.domain.operations import (
    create_operation,
    reconcile_availability_operation,
)
from apps.ocpp.models import InboundProtocolRequest, ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from tests.apps.ocpp.builders import charger, protocol_operation


class AvailabilityOperationReconciliationTests:
    @pytest.fixture(autouse=True)
    def _setup(self, db) -> None:
        self.charger = charger("reconcile-availability")
        self.attempt_at = datetime(2026, 9, 24, 20, tzinfo=timezone.utc)

    def _ambiguous(
        self,
        *,
        version: ProtocolVersion,
        payload: dict[str, object],
    ) -> ProtocolOperation:
        return protocol_operation(
            self.charger,
            "ChangeAvailability",
            version=version,
            request_payload=payload,
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
            attempts=1,
            attempt_at=self.attempt_at,
        )

    def _status(
        self,
        *,
        version: ProtocolVersion,
        payload: dict[str, object],
        received_at: datetime | None = None,
        suffix: str,
    ) -> InboundProtocolRequest:
        request = InboundProtocolRequest.objects.create(
            charger=self.charger,
            version=version.value,
            direction=Direction.CHARGE_POINT_TO_CSMS.value,
            action="StatusNotification",
            unique_id=f"status-{suffix}",
            fingerprint=f"fingerprint-{suffix}",
            identity_key=f"identity-{suffix}",
            request_payload=payload,
            status=InboundProtocolRequest.Status.COMPLETED,
        )
        if received_at is not None:
            InboundProtocolRequest.objects.filter(pk=request.pk).update(
                received_at=received_at
            )
            request.refresh_from_db()
        return request

    def test_v16_fresh_unavailable_proves_inoperative_achieved(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            payload={"connectorId": 1, "type": "Inoperative"},
        )
        evidence = self._status(
            version=ProtocolVersion.OCPP_16,
            payload={"connectorId": 1, "status": "Unavailable"},
            received_at=self.attempt_at + timedelta(seconds=1),
            suffix="v16-inoperative",
        )

        result = reconcile_availability_operation(operation)

        assert result.status == ProtocolOperation.Status.COMPLETED
        assert (
            result.reconciliation_resolution
            == ProtocolOperation.ReconciliationResolution.ACHIEVED
        )
        assert str(evidence.pk) in result.reconciliation_basis
        assert (
            ProtocolOperation.objects.filter(action="ChangeAvailability").count()
            == 1
        )

    def test_v16_contrary_fresh_state_creates_deliberate_replacement(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            payload={"connectorId": 1, "type": "Inoperative"},
        )
        self._status(
            version=ProtocolVersion.OCPP_16,
            payload={"connectorId": 1, "status": "Available"},
            received_at=self.attempt_at + timedelta(seconds=1),
            suffix="v16-contrary",
        )

        result = reconcile_availability_operation(operation)

        assert result.status == ProtocolOperation.Status.COMPLETED
        assert (
            result.reconciliation_resolution
            == ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED
        )
        replacement = (
            ProtocolOperation.objects.filter(action="ChangeAvailability")
            .exclude(pk=operation.pk)
            .get()
        )
        assert replacement.status == ProtocolOperation.Status.PENDING
        assert replacement.request_payload == operation.request_payload
        assert str(replacement.pk) in result.reconciliation_basis

    def test_faulted_status_remains_ambiguous(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            payload={"connectorId": 1, "type": "Operative"},
        )
        self._status(
            version=ProtocolVersion.OCPP_16,
            payload={"connectorId": 1, "status": "Faulted"},
            received_at=self.attempt_at + timedelta(seconds=1),
            suffix="faulted",
        )

        result = reconcile_availability_operation(operation)

        assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
        assert result.reconciled_at is None
        self.assertEqual(
            ProtocolOperation.objects.filter(action="ChangeAvailability").count(),
            1,
        )

    def test_status_before_attempt_is_not_reconciliation_evidence(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            payload={"connectorId": 1, "type": "Inoperative"},
        )
        self._status(
            version=ProtocolVersion.OCPP_16,
            payload={"connectorId": 1, "status": "Unavailable"},
            received_at=self.attempt_at - timedelta(seconds=1),
            suffix="stale",
        )

        result = reconcile_availability_operation(operation)

        assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
        assert result.reconciled_at is None

    def test_v201_evse_connector_status_proves_operative(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            payload={
                "operationalStatus": "Operative",
                "evse": {"id": 2, "connectorId": 3},
            },
        )
        evidence = self._status(
            version=ProtocolVersion.OCPP_201,
            payload={
                "evseId": 2,
                "connectorId": 3,
                "connectorStatus": "Occupied",
            },
            received_at=self.attempt_at + timedelta(seconds=1),
            suffix="v201-operative",
        )

        result = reconcile_availability_operation(operation)

        assert result.status == ProtocolOperation.Status.COMPLETED
        assert (
            result.reconciliation_resolution
            == ProtocolOperation.ReconciliationResolution.ACHIEVED
        )
        assert str(evidence.pk) in result.reconciliation_basis

    def test_station_wide_change_remains_ambiguous_without_aggregate_evidence(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            payload={"connectorId": 0, "type": "Inoperative"},
        )
        self._status(
            version=ProtocolVersion.OCPP_16,
            payload={"connectorId": 1, "status": "Unavailable"},
            received_at=self.attempt_at + timedelta(seconds=1),
            suffix="station-wide",
        )

        result = reconcile_availability_operation(operation)

        assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
        assert result.reconciled_at is None
