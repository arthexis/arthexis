from django.test import SimpleTestCase

from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.registry import ACTION_REGISTRY, ALL_ACTIONS, resolve_action

EXPECTED_ACTIONS = {
    (ProtocolVersion.OCPP_16, Direction.CHARGE_POINT_TO_CSMS): {
        "Authorize",
        "BootNotification",
        "DataTransfer",
        "DiagnosticsStatusNotification",
        "FirmwareStatusNotification",
        "Heartbeat",
        "MeterValues",
        "StartTransaction",
        "StatusNotification",
        "StopTransaction",
    },
    (ProtocolVersion.OCPP_16, Direction.CSMS_TO_CHARGE_POINT): {
        "CancelReservation",
        "ChangeAvailability",
        "ChangeConfiguration",
        "ClearChargingProfile",
        "DataTransfer",
        "GetCompositeSchedule",
        "GetConfiguration",
        "GetDiagnostics",
        "GetLocalListVersion",
        "RemoteStartTransaction",
        "RemoteStopTransaction",
        "Reset",
        "ReserveNow",
        "SendLocalList",
        "SetChargingProfile",
        "TriggerMessage",
        "UnlockConnector",
        "UpdateFirmware",
    },
    (ProtocolVersion.OCPP_201, Direction.CHARGE_POINT_TO_CSMS): {
        "Authorize",
        "BootNotification",
        "ClearedChargingLimit",
        "CostUpdated",
        "DataTransfer",
        "FirmwareStatusNotification",
        "Get15118EVCertificate",
        "GetCertificateStatus",
        "Heartbeat",
        "LogStatusNotification",
        "MeterValues",
        "NotifyChargingLimit",
        "NotifyCustomerInformation",
        "NotifyDisplayMessages",
        "NotifyEVChargingNeeds",
        "NotifyEVChargingSchedule",
        "NotifyEvent",
        "NotifyMonitoringReport",
        "NotifyReport",
        "PublishFirmwareStatusNotification",
        "ReportChargingProfiles",
        "ReservationStatusUpdate",
        "SecurityEventNotification",
        "SignCertificate",
        "StatusNotification",
        "TransactionEvent",
    },
    (ProtocolVersion.OCPP_201, Direction.CSMS_TO_CHARGE_POINT): {
        "CancelReservation",
        "CertificateSigned",
        "ChangeAvailability",
        "ClearChargingProfile",
        "ClearDisplayMessage",
        "ClearVariableMonitoring",
        "CustomerInformation",
        "DataTransfer",
        "DeleteCertificate",
        "GetBaseReport",
        "GetCompositeSchedule",
        "GetDisplayMessages",
        "GetInstalledCertificateIds",
        "GetLocalListVersion",
        "GetLog",
        "GetReport",
        "GetVariables",
        "InstallCertificate",
        "PublishFirmware",
        "RequestStartTransaction",
        "RequestStopTransaction",
        "ReserveNow",
        "Reset",
        "SendLocalList",
        "SetChargingProfile",
        "SetDisplayMessage",
        "SetMonitoringBase",
        "SetMonitoringLevel",
        "SetVariableMonitoring",
        "SetVariables",
        "TriggerMessage",
        "UnlockConnector",
        "UpdateFirmware",
    },
}


class ActionMatrixTests(SimpleTestCase):
    def test_registry_matches_the_frozen_action_matrix(self) -> None:
        for key, expected_actions in EXPECTED_ACTIONS.items():
            version, direction = key
            registered_actions = {
                contract.action
                for contract in ALL_ACTIONS
                if contract.version == version and contract.direction == direction
            }
            self.assertEqual(registered_actions, expected_actions)

    def test_every_contract_has_unique_protocol_metadata(self) -> None:
        self.assertEqual(len(ACTION_REGISTRY), len(ALL_ACTIONS))
        for contract in ALL_ACTIONS:
            self.assertTrue(contract.persistence_owner)
            self.assertEqual(contract.request_contract, f"{contract.action}Request")
            self.assertEqual(contract.response_contract, f"{contract.action}Response")
            self.assertEqual(contract.call_error_contract, "CallError")

    def test_version_and_direction_select_the_action_contract(self) -> None:
        self.assertIsNotNone(
            resolve_action(
                version=ProtocolVersion.OCPP_16,
                direction=Direction.CSMS_TO_CHARGE_POINT,
                action="GetConfiguration",
            )
        )
        self.assertIsNone(
            resolve_action(
                version=ProtocolVersion.OCPP_16,
                direction=Direction.CSMS_TO_CHARGE_POINT,
                action="GetVariables",
            )
        )
