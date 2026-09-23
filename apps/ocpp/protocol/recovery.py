"""Action-specific outbound recovery policy.

Policies are deliberately conservative. SAFE_RETRY is reserved for synchronous,
observational requests whose duplicate execution does not mutate charger state.
RECONCILE means domain/configuration evidence should be checked before deciding
whether another command is appropriate. MANUAL covers opaque or consequential
operations that must never be retried automatically from ambiguity.
"""

from apps.ocpp.protocol.contracts import ProtocolVersion

SAFE_RETRY = "safe_retry"
RECONCILE = "reconcile"
MANUAL = "manual"

V16_SAFE_RETRY = frozenset(
    {
        "GetCompositeSchedule",
        "GetConfiguration",
        "GetLocalListVersion",
    }
)

V16_RECONCILE = frozenset(
    {
        "CancelReservation",
        "ChangeAvailability",
        "ChangeConfiguration",
        "ClearChargingProfile",
        "RemoteStartTransaction",
        "RemoteStopTransaction",
        "ReserveNow",
        "SendLocalList",
        "SetChargingProfile",
    }
)

V201_SAFE_RETRY = frozenset(
    {
        "GetCompositeSchedule",
        "GetDisplayMessages",
        "GetInstalledCertificateIds",
        "GetLocalListVersion",
        "GetVariables",
    }
)

V201_RECONCILE = frozenset(
    {
        "CancelReservation",
        "ChangeAvailability",
        "ClearChargingProfile",
        "ClearDisplayMessage",
        "ClearVariableMonitoring",
        "DeleteCertificate",
        "GetBaseReport",
        "GetReport",
        "InstallCertificate",
        "RequestStartTransaction",
        "RequestStopTransaction",
        "ReserveNow",
        "SendLocalList",
        "SetChargingProfile",
        "SetDisplayMessage",
        "SetMonitoringBase",
        "SetMonitoringLevel",
        "SetVariableMonitoring",
        "SetVariables",
    }
)


def recovery_policy_for(*, version: ProtocolVersion, action: str) -> str:
    """Return the conservative recovery policy for one outbound action."""
    if version == ProtocolVersion.OCPP_16:
        if action in V16_SAFE_RETRY:
            return SAFE_RETRY
        if action in V16_RECONCILE:
            return RECONCILE
        return MANUAL

    if version == ProtocolVersion.OCPP_201:
        if action in V201_SAFE_RETRY:
            return SAFE_RETRY
        if action in V201_RECONCILE:
            return RECONCILE
        return MANUAL

    return MANUAL
