from datetime import datetime, timedelta, timezone

import pytest

from apps.ocpp.domain.operations import reconcile_profile_operation
from apps.ocpp.domain.profiles import record_profile
from apps.ocpp.models import ChargingProfile, ProtocolOperation
from apps.ocpp.protocol.contracts import ProtocolVersion
from tests.apps.ocpp.builders import charger, protocol_operation

pytestmark = pytest.mark.django_db


@pytest.fixture
def profile_context():
    selected = charger("reconcile-profile")
    attempt_at = datetime(2026, 9, 24, 20, tzinfo=timezone.utc)

    def ambiguous(
        *,
        version: ProtocolVersion,
        action: str,
        payload: dict[str, object],
    ) -> ProtocolOperation:
        return protocol_operation(
            selected,
            action,
            version=version,
            request_payload=payload,
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
            attempts=1,
            attempt_at=attempt_at,
        )

    def profile(
        *,
        remote_id: str,
        active: bool = True,
        stack_level: int = 2,
        purpose: str = "TxDefaultProfile",
        kind: str = "Absolute",
        updated_at: datetime | None = None,
    ) -> ChargingProfile:
        value = record_profile(
            charger=selected,
            remote_id=remote_id,
            purpose=purpose,
            kind=kind,
            payload={"chargingSchedule": {}},
            stack_level=stack_level,
        )
        ChargingProfile.objects.filter(pk=value.pk).update(active=active)
        if updated_at is not None:
            ChargingProfile.objects.filter(pk=value.pk).update(updated_at=updated_at)
        value.refresh_from_db()
        return value

    return attempt_at, ambiguous, profile


def test_v16_fresh_matching_profile_proves_set_achieved(profile_context) -> None:
    attempt_at, ambiguous, profile = profile_context
    operation = ambiguous(
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
    profile(remote_id="11", updated_at=attempt_at + timedelta(seconds=1))

    result = reconcile_profile_operation(operation)

    assert result.status == ProtocolOperation.Status.COMPLETED
    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )
    assert ProtocolOperation.objects.filter(action="SetChargingProfile").count() == 1


def test_v16_fresh_inactive_profile_creates_set_replacement(profile_context) -> None:
    attempt_at, ambiguous, profile = profile_context
    operation = ambiguous(
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
    profile(
        remote_id="12",
        active=False,
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_profile_operation(operation)

    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED
    )
    replacement = (
        ProtocolOperation.objects.filter(action="SetChargingProfile")
        .exclude(pk=operation.pk)
        .get()
    )
    assert replacement.request_payload == operation.request_payload
    assert replacement.status == ProtocolOperation.Status.PENDING


def test_stale_profile_does_not_resolve_set(profile_context) -> None:
    attempt_at, ambiguous, profile = profile_context
    operation = ambiguous(
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
    profile(remote_id="13", updated_at=attempt_at - timedelta(seconds=1))

    result = reconcile_profile_operation(operation)

    assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
    assert result.reconciled_at is None


def test_v16_fresh_inactive_profile_proves_clear_achieved(profile_context) -> None:
    attempt_at, ambiguous, profile = profile_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ClearChargingProfile",
        payload={"id": 14},
    )
    profile(
        remote_id="14",
        active=False,
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_profile_operation(operation)

    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )
    assert ProtocolOperation.objects.filter(action="ClearChargingProfile").count() == 1


def test_v16_fresh_active_profile_creates_clear_replacement(profile_context) -> None:
    attempt_at, ambiguous, profile = profile_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ClearChargingProfile",
        payload={"id": 15},
    )
    profile(
        remote_id="15",
        active=True,
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_profile_operation(operation)

    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED
    )
    replacement = (
        ProtocolOperation.objects.filter(action="ClearChargingProfile")
        .exclude(pk=operation.pk)
        .get()
    )
    assert replacement.status == ProtocolOperation.Status.PENDING


def test_broad_clear_without_profile_id_remains_ambiguous(profile_context) -> None:
    attempt_at, ambiguous, profile = profile_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ClearChargingProfile",
        payload={},
    )
    profile(
        remote_id="16",
        active=False,
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_profile_operation(operation)

    assert result.status == ProtocolOperation.Status.RECOVERY_REQUIRED
    assert result.reconciled_at is None


def test_v201_profile_identity_uses_charging_profile_id(profile_context) -> None:
    attempt_at, ambiguous, profile = profile_context
    operation = ambiguous(
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
    profile(remote_id="17", updated_at=attempt_at + timedelta(seconds=1))

    result = reconcile_profile_operation(operation)

    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )


def test_v201_clear_uses_charging_profile_criteria_id(profile_context) -> None:
    attempt_at, ambiguous, profile = profile_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_201,
        action="ClearChargingProfile",
        payload={"chargingProfileCriteria": {"chargingProfileId": 18}},
    )
    profile(
        remote_id="18",
        active=False,
        updated_at=attempt_at + timedelta(seconds=1),
    )

    result = reconcile_profile_operation(operation)

    assert (
        result.reconciliation_resolution
        == ProtocolOperation.ReconciliationResolution.ACHIEVED
    )


def test_repeated_sweep_does_not_duplicate_replacement(profile_context) -> None:
    attempt_at, ambiguous, profile = profile_context
    operation = ambiguous(
        version=ProtocolVersion.OCPP_16,
        action="ClearChargingProfile",
        payload={"id": 19},
    )
    profile(
        remote_id="19",
        active=True,
        updated_at=attempt_at + timedelta(seconds=1),
    )

    first = reconcile_profile_operation(operation)
    second = reconcile_profile_operation(operation)

    assert first.pk == second.pk
    assert ProtocolOperation.objects.filter(action="ClearChargingProfile").count() == 2
