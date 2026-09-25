from datetime import datetime, timedelta, timezone

from django.test import TestCase

from apps.ocpp.domain.operations import create_operation, reconcile_profile_operation
from apps.ocpp.domain.profiles import record_profile
from apps.ocpp.models import ChargingProfile, ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from tests.apps.ocpp.builders import charger


class ChargingProfileOperationReconciliationTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("reconcile-profile")
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

    def _profile(
        self,
        *,
        remote_id: str,
        active: bool = True,
        stack_level: int = 2,
        purpose: str = "TxDefaultProfile",
        kind: str = "Absolute",
        updated_at: datetime | None = None,
    ) -> ChargingProfile:
        profile = record_profile(
            charger=self.charger,
            remote_id=remote_id,
            purpose=purpose,
            kind=kind,
            payload={"chargingSchedule": {}},
            stack_level=stack_level,
        )
        ChargingProfile.objects.filter(pk=profile.pk).update(active=active)
        if updated_at is not None:
            ChargingProfile.objects.filter(pk=profile.pk).update(updated_at=updated_at)
        profile.refresh_from_db()
        return profile

    def test_v16_fresh_matching_profile_proves_set_achieved(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="SetChargingProfile",
            payload={
                "connectorId": 1,
                "csChargingProfiles": {
                    "chargingProfileId": 11,
                    "stackLevel": 2,
                    "chargingProfilePurpose": "TxDefaultProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": {},
                },
            },
        )
        self._profile(
            remote_id="11",
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_profile_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertEqual(
            ProtocolOperation.objects.filter(action="SetChargingProfile").count(),
            1,
        )

    def test_v16_fresh_inactive_profile_creates_set_replacement(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="SetChargingProfile",
            payload={
                "connectorId": 1,
                "csChargingProfiles": {
                    "chargingProfileId": 12,
                    "stackLevel": 2,
                    "chargingProfilePurpose": "TxDefaultProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": {},
                },
            },
        )
        self._profile(
            remote_id="12",
            active=False,
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_profile_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED,
        )
        replacement = (
            ProtocolOperation.objects.filter(action="SetChargingProfile")
            .exclude(pk=operation.pk)
            .get()
        )
        self.assertEqual(replacement.request_payload, operation.request_payload)
        self.assertEqual(replacement.status, ProtocolOperation.Status.PENDING)

    def test_stale_profile_does_not_resolve_set(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="SetChargingProfile",
            payload={
                "connectorId": 1,
                "csChargingProfiles": {
                    "chargingProfileId": 13,
                    "stackLevel": 2,
                    "chargingProfilePurpose": "TxDefaultProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": {},
                },
            },
        )
        self._profile(
            remote_id="13",
            updated_at=self.attempt_at - timedelta(seconds=1),
        )

        result = reconcile_profile_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.RECOVERY_REQUIRED)
        self.assertIsNone(result.reconciled_at)

    def test_v16_fresh_inactive_profile_proves_clear_achieved(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ClearChargingProfile",
            payload={"id": 14},
        )
        self._profile(
            remote_id="14",
            active=False,
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_profile_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )
        self.assertEqual(
            ProtocolOperation.objects.filter(action="ClearChargingProfile").count(),
            1,
        )

    def test_v16_fresh_active_profile_creates_clear_replacement(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ClearChargingProfile",
            payload={"id": 15},
        )
        self._profile(
            remote_id="15",
            active=True,
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_profile_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED,
        )
        replacement = (
            ProtocolOperation.objects.filter(action="ClearChargingProfile")
            .exclude(pk=operation.pk)
            .get()
        )
        self.assertEqual(replacement.status, ProtocolOperation.Status.PENDING)

    def test_broad_clear_without_profile_id_remains_ambiguous(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ClearChargingProfile",
            payload={},
        )
        self._profile(
            remote_id="16",
            active=False,
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_profile_operation(operation)

        self.assertEqual(result.status, ProtocolOperation.Status.RECOVERY_REQUIRED)
        self.assertIsNone(result.reconciled_at)

    def test_v201_profile_identity_uses_charging_profile_id(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            action="SetChargingProfile",
            payload={
                "evseId": 2,
                "chargingProfile": {
                    "id": 17,
                    "stackLevel": 2,
                    "chargingProfilePurpose": "TxDefaultProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": {},
                },
            },
        )
        self._profile(
            remote_id="17",
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_profile_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )

    def test_v201_clear_uses_charging_profile_criteria_id(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_201,
            action="ClearChargingProfile",
            payload={"chargingProfileCriteria": {"chargingProfileId": 18}},
        )
        self._profile(
            remote_id="18",
            active=False,
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        result = reconcile_profile_operation(operation)

        self.assertEqual(
            result.reconciliation_resolution,
            ProtocolOperation.ReconciliationResolution.ACHIEVED,
        )

    def test_repeated_sweep_does_not_duplicate_replacement(self) -> None:
        operation = self._ambiguous(
            version=ProtocolVersion.OCPP_16,
            action="ClearChargingProfile",
            payload={"id": 19},
        )
        self._profile(
            remote_id="19",
            active=True,
            updated_at=self.attempt_at + timedelta(seconds=1),
        )

        first = reconcile_profile_operation(operation)
        second = reconcile_profile_operation(operation)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            ProtocolOperation.objects.filter(action="ClearChargingProfile").count(),
            2,
        )
