"""Frozen OCPP 1.6 action matrix, split by message direction."""

from apps.ocpp.protocol.contracts import (
    ActionContract,
    Direction,
    ProtocolVersion,
    action_contract,
)


def _actions(
    direction: Direction, action_owners: tuple[tuple[str, str], ...]
) -> tuple[ActionContract, ...]:
    return tuple(
        action_contract(
            version=ProtocolVersion.OCPP_16,
            direction=direction,
            action=action,
            persistence_owner=owner,
        )
        for action, owner in action_owners
    )


INBOUND_ACTIONS = _actions(
    Direction.CHARGE_POINT_TO_CSMS,
    (
        ("Authorize", "authorization"),
        ("BootNotification", "assets"),
        ("DataTransfer", "operations"),
        ("DiagnosticsStatusNotification", "operations"),
        ("FirmwareStatusNotification", "operations"),
        ("Heartbeat", "assets"),
        ("MeterValues", "sessions"),
        ("StartTransaction", "sessions"),
        ("StatusNotification", "assets"),
        ("StopTransaction", "sessions"),
    ),
)

OUTBOUND_ACTIONS = _actions(
    Direction.CSMS_TO_CHARGE_POINT,
    (
        ("CancelReservation", "reservations"),
        ("ChangeAvailability", "assets"),
        ("ChangeConfiguration", "configuration"),
        ("ClearChargingProfile", "profiles"),
        ("DataTransfer", "operations"),
        ("GetCompositeSchedule", "profiles"),
        ("GetConfiguration", "configuration"),
        ("GetDiagnostics", "operations"),
        ("GetLocalListVersion", "authorization"),
        ("RemoteStartTransaction", "sessions"),
        ("RemoteStopTransaction", "sessions"),
        ("Reset", "assets"),
        ("ReserveNow", "reservations"),
        ("SendLocalList", "authorization"),
        ("SetChargingProfile", "profiles"),
        ("TriggerMessage", "operations"),
        ("UnlockConnector", "assets"),
        ("UpdateFirmware", "operations"),
    ),
)

ACTIONS = (*INBOUND_ACTIONS, *OUTBOUND_ACTIONS)
