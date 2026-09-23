from django.test import TestCase

from apps.ocpp.domain.operations import (
    create_operation,
    prepare_safe_retry,
    require_operation_recovery,
)
from apps.ocpp.models import ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.recovery import (
    MANUAL,
    RECONCILE,
    SAFE_RETRY,
    recovery_policy_for,
)
from apps.ocpp.protocol.v16.matrix import OUTBOUND_ACTIONS as V16_OUTBOUND_ACTIONS
from apps.ocpp.protocol.v201.matrix import OUTBOUND_ACTIONS as V201_OUTBOUND_ACTIONS
from tests.apps.ocpp.builders import charger


class OutboundRecoveryPolicyTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("recovery-policy")

    def _operation(
        self,
        *,
        version: ProtocolVersion,
        action: str,
    ) -> ProtocolOperation:
        return create_operation(
            charger=self.charger,
            version=version,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action=action,
            request_payload={},
        )

    def test_every_outbound_action_has_a_supported_policy(self) -> None:
        allowed = {SAFE_RETRY, RECONCILE, MANUAL}
        for contract in (*V16_OUTBOUND_ACTIONS, *V201_OUTBOUND_ACTIONS):
            self.assertIn(
                recovery_policy_for(
                    version=contract.version,
                    action=contract.action,
                ),
                allowed,
                msg=f"{contract.version.value} {contract.action}",
            )

    def test_safe_retry_is_restricted_to_observational_requests(self) -> None:
        cases = (
            (ProtocolVersion.OCPP_16, "GetConfiguration"),
            (ProtocolVersion.OCPP_16, "GetCompositeSchedule"),
            (ProtocolVersion.OCPP_16, "GetLocalListVersion"),
            (ProtocolVersion.OCPP_201, "GetVariables"),
            (ProtocolVersion.OCPP_201, "GetCompositeSchedule"),
            (ProtocolVersion.OCPP_201, "GetInstalledCertificateIds"),
        )
        for version, action in cases:
            with self.subTest(version=version, action=action):
                self.assertEqual(
                    recovery_policy_for(version=version, action=action),
                    SAFE_RETRY,
                )

    def test_state_changing_actions_require_reconciliation(self) -> None:
        cases = (
            (ProtocolVersion.OCPP_16, "RemoteStartTransaction"),
            (ProtocolVersion.OCPP_16, "RemoteStopTransaction"),
            (ProtocolVersion.OCPP_16, "ChangeAvailability"),
            (ProtocolVersion.OCPP_16, "ReserveNow"),
            (ProtocolVersion.OCPP_16, "SetChargingProfile"),
            (ProtocolVersion.OCPP_201, "RequestStartTransaction"),
            (ProtocolVersion.OCPP_201, "RequestStopTransaction"),
            (ProtocolVersion.OCPP_201, "ChangeAvailability"),
            (ProtocolVersion.OCPP_201, "SetVariables"),
            (ProtocolVersion.OCPP_201, "InstallCertificate"),
        )
        for version, action in cases:
            with self.subTest(version=version, action=action):
                self.assertEqual(
                    recovery_policy_for(version=version, action=action),
                    RECONCILE,
                )

    def test_opaque_or_consequential_actions_are_manual(self) -> None:
        cases = (
            (ProtocolVersion.OCPP_16, "DataTransfer"),
            (ProtocolVersion.OCPP_16, "Reset"),
            (ProtocolVersion.OCPP_16, "UnlockConnector"),
            (ProtocolVersion.OCPP_16, "UpdateFirmware"),
            (ProtocolVersion.OCPP_201, "DataTransfer"),
            (ProtocolVersion.OCPP_201, "Reset"),
            (ProtocolVersion.OCPP_201, "UnlockConnector"),
            (ProtocolVersion.OCPP_201, "PublishFirmware"),
            (ProtocolVersion.OCPP_201, "UpdateFirmware"),
        )
        for version, action in cases:
            with self.subTest(version=version, action=action):
                self.assertEqual(
                    recovery_policy_for(version=version, action=action),
                    MANUAL,
                )

    def test_new_operation_persists_its_policy_snapshot(self) -> None:
        safe = self._operation(
            version=ProtocolVersion.OCPP_16,
            action="GetConfiguration",
        )
        reconcile = self._operation(
            version=ProtocolVersion.OCPP_16,
            action="RemoteStopTransaction",
        )
        manual = self._operation(
            version=ProtocolVersion.OCPP_16,
            action="Reset",
        )

        self.assertEqual(
            safe.recovery_policy,
            ProtocolOperation.RecoveryPolicy.SAFE_RETRY,
        )
        self.assertEqual(
            reconcile.recovery_policy,
            ProtocolOperation.RecoveryPolicy.RECONCILE,
        )
        self.assertEqual(
            manual.recovery_policy,
            ProtocolOperation.RecoveryPolicy.MANUAL,
        )

    def test_safe_retry_can_return_ambiguous_operation_to_pending(self) -> None:
        operation = self._operation(
            version=ProtocolVersion.OCPP_16,
            action="GetConfiguration",
        )
        operation.status = ProtocolOperation.Status.DELIVERING
        operation.attempt_count = 1
        operation.save(update_fields=("status", "attempt_count"))
        require_operation_recovery(operation, description="response lost")

        recovered = prepare_safe_retry(operation)

        self.assertIsNotNone(recovered)
        operation.refresh_from_db()
        self.assertEqual(operation.status, ProtocolOperation.Status.PENDING)
        self.assertEqual(operation.attempt_count, 1)
        self.assertIsNone(operation.completed_at)

    def test_reconcile_policy_cannot_be_blindly_retried(self) -> None:
        operation = self._operation(
            version=ProtocolVersion.OCPP_16,
            action="RemoteStopTransaction",
        )
        operation.status = ProtocolOperation.Status.DELIVERING
        operation.save(update_fields=("status",))
        require_operation_recovery(operation, description="connection lost")

        self.assertIsNone(prepare_safe_retry(operation))
        operation.refresh_from_db()
        self.assertEqual(
            operation.status,
            ProtocolOperation.Status.RECOVERY_REQUIRED,
        )

    def test_manual_policy_cannot_be_blindly_retried(self) -> None:
        operation = self._operation(
            version=ProtocolVersion.OCPP_16,
            action="Reset",
        )
        operation.status = ProtocolOperation.Status.DELIVERING
        operation.save(update_fields=("status",))
        require_operation_recovery(operation, description="connection lost")

        self.assertIsNone(prepare_safe_retry(operation))
        operation.refresh_from_db()
        self.assertEqual(
            operation.status,
            ProtocolOperation.Status.RECOVERY_REQUIRED,
        )
