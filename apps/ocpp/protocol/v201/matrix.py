"""Frozen retained OCPP 2.0.1 action contracts."""

from apps.ocpp.protocol.contracts import (
    ActionContract,
    Direction,
    ProtocolVersion,
    action_contract,
)


def _actions(
    direction: Direction, values: tuple[tuple[str, str], ...]
) -> tuple[ActionContract, ...]:
    return tuple(
        action_contract(
            version=ProtocolVersion.OCPP_201,
            direction=direction,
            action=action,
            persistence_owner=owner,
        )
        for action, owner in values
    )


INBOUND_ACTIONS = _actions(
    Direction.CHARGE_POINT_TO_CSMS,
    (
        ("Authorize", "authorization"),
        ("BootNotification", "assets"),
        ("ClearedChargingLimit", "profiles"),
        ("CostUpdated", "notifications"),
        ("DataTransfer", "operations"),
        ("FirmwareStatusNotification", "operations"),
        ("Get15118EVCertificate", "certificates"),
        ("GetCertificateStatus", "certificates"),
        ("Heartbeat", "assets"),
        ("LogStatusNotification", "operations"),
        ("MeterValues", "sessions"),
        ("NotifyChargingLimit", "profiles"),
        ("NotifyCustomerInformation", "notifications"),
        ("NotifyDisplayMessages", "notifications"),
        ("NotifyEVChargingNeeds", "profiles"),
        ("NotifyEVChargingSchedule", "profiles"),
        ("NotifyEvent", "notifications"),
        ("NotifyMonitoringReport", "monitoring"),
        ("NotifyReport", "monitoring"),
        ("PublishFirmwareStatusNotification", "operations"),
        ("ReportChargingProfiles", "profiles"),
        ("ReservationStatusUpdate", "reservations"),
        ("SecurityEventNotification", "certificates"),
        ("SignCertificate", "certificates"),
        ("StatusNotification", "assets"),
        ("TransactionEvent", "sessions"),
    ),
)

OUTBOUND_ACTIONS = _actions(
    Direction.CSMS_TO_CHARGE_POINT,
    (
        ("CancelReservation", "reservations"),
        ("CertificateSigned", "certificates"),
        ("ChangeAvailability", "assets"),
        ("ClearChargingProfile", "profiles"),
        ("ClearDisplayMessage", "notifications"),
        ("ClearVariableMonitoring", "monitoring"),
        ("CustomerInformation", "notifications"),
        ("DataTransfer", "operations"),
        ("DeleteCertificate", "certificates"),
        ("GetBaseReport", "monitoring"),
        ("GetCompositeSchedule", "profiles"),
        ("GetDisplayMessages", "notifications"),
        ("GetInstalledCertificateIds", "certificates"),
        ("GetLocalListVersion", "authorization"),
        ("GetLog", "operations"),
        ("GetReport", "monitoring"),
        ("GetVariables", "configuration"),
        ("InstallCertificate", "certificates"),
        ("PublishFirmware", "operations"),
        ("RequestStartTransaction", "sessions"),
        ("RequestStopTransaction", "sessions"),
        ("ReserveNow", "reservations"),
        ("Reset", "assets"),
        ("SendLocalList", "authorization"),
        ("SetChargingProfile", "profiles"),
        ("SetDisplayMessage", "notifications"),
        ("SetMonitoringBase", "monitoring"),
        ("SetMonitoringLevel", "monitoring"),
        ("SetVariableMonitoring", "monitoring"),
        ("SetVariables", "configuration"),
        ("TriggerMessage", "operations"),
        ("UnlockConnector", "assets"),
        ("UpdateFirmware", "operations"),
    ),
)
