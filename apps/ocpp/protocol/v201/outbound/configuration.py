"""OCPP 2.0.1 configuration and monitoring request validation."""

from apps.ocpp.protocol.v201.outbound.common import require

VALIDATORS = {
    "ClearVariableMonitoring": lambda payload: require(payload, "id"),
    "GetVariables": lambda payload: require(payload, "getVariableData"),
    "SetMonitoringBase": lambda payload: require(payload, "type"),
    "SetMonitoringLevel": lambda payload: require(payload, "severity"),
    "SetVariableMonitoring": lambda payload: require(payload, "setVariableMonitoring"),
    "SetVariables": lambda payload: require(payload, "setVariableData"),
}
