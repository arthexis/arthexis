"""Stateful retained inbound-action scenarios for both supported OCPP versions."""

from dataclasses import dataclass

from apps.ocpp.models import Charger
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.simulator.client import OcppSimulator


@dataclass(frozen=True)
class AuthorizationScenarioResult:
    """One observed authorization result in scenario order."""

    id_tag: str
    status: str


async def run_v16_authorization_scenario(
    charger: Charger,
    id_tags: tuple[str, ...],
) -> tuple[AuthorizationScenarioResult, ...]:
    """Run an OCPP 1.6 authorization matrix and report actual CSMS decisions."""
    client = OcppSimulator(charger=charger, version=ProtocolVersion.OCPP_16)
    results: list[AuthorizationScenarioResult] = []
    for id_tag in id_tags:
        response = await client.call("Authorize", {"idTag": id_tag})
        info = response.payload.get("idTagInfo")
        if not isinstance(info, dict):
            raise ValueError("Authorize response is missing idTagInfo")
        status = info.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError("Authorize response is missing status")
        results.append(AuthorizationScenarioResult(id_tag=id_tag, status=status))
    return tuple(results)


async def run_v16_scenario(charger: Charger) -> tuple[str, ...]:
    """Exercise every retained OCPP 1.6 charge-point-to-CSMS action."""
    client = OcppSimulator(charger=charger, version=ProtocolVersion.OCPP_16)
    completed: list[str] = []

    await _call(client, completed, "BootNotification", _v16_boot())
    await _call(client, completed, "Heartbeat", {})
    await _call(client, completed, "Authorize", {"idTag": "sim-card"})
    await _call(
        client,
        completed,
        "StatusNotification",
        {"connectorId": 1, "status": "Preparing"},
    )
    started = await _call(
        client,
        completed,
        "StartTransaction",
        {
            "connectorId": 1,
            "idTag": "sim-card",
            "meterStart": 100,
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    transaction_id = started.payload["transactionId"]
    await _call(
        client,
        completed,
        "MeterValues",
        {
            "transactionId": transaction_id,
            "meterValue": [
                {
                    "sampledValue": [{"value": "125"}],
                    "timestamp": "2026-01-01T00:05:00Z",
                }
            ],
        },
    )
    await _call(client, completed, "DataTransfer", {"vendorId": "SIM"})
    await _call(
        client, completed, "DiagnosticsStatusNotification", {"status": "Uploaded"}
    )
    await _call(
        client, completed, "FirmwareStatusNotification", {"status": "Downloaded"}
    )
    await _call(
        client,
        completed,
        "StopTransaction",
        {
            "meterStop": 150,
            "timestamp": "2026-01-01T00:10:00Z",
            "transactionId": transaction_id,
        },
    )
    return tuple(completed)


async def run_v201_scenario(charger: Charger) -> tuple[str, ...]:
    """Exercise every retained OCPP 2.0.1 charge-point-to-CSMS action."""
    client = OcppSimulator(charger=charger, version=ProtocolVersion.OCPP_201)
    completed: list[str] = []
    payloads = _v201_payloads()

    for action in (
        "BootNotification",
        "Heartbeat",
        "Authorize",
        "StatusNotification",
        "TransactionEvent",
        "MeterValues",
    ):
        await _call(client, completed, action, payloads.pop(action))
    for action, payload in payloads.items():
        await _call(client, completed, action, payload)
    return tuple(completed)


async def _call(
    client: OcppSimulator,
    completed: list[str],
    action: str,
    payload: dict[str, object],
):
    response = await client.call(action, payload)
    completed.append(action)
    return response


def _v16_boot() -> dict[str, object]:
    return {"chargePointModel": "Simulator", "chargePointVendor": "Arthexis"}


def _v201_payloads() -> dict[str, dict[str, object]]:
    return {
        "Authorize": {"idToken": {"idToken": "sim-card"}},
        "BootNotification": {
            "chargingStation": {"model": "Simulator", "vendorName": "Arthexis"}
        },
        "ClearedChargingLimit": {},
        "CostUpdated": {},
        "DataTransfer": {"vendorId": "SIM"},
        "FirmwareStatusNotification": {"status": "Downloaded"},
        "Get15118EVCertificate": {},
        "GetCertificateStatus": {},
        "Heartbeat": {},
        "LogStatusNotification": {"status": "Uploaded"},
        "MeterValues": {
            "meterValue": [{"sampledValue": [{"value": "10"}]}],
            "transactionInfo": {"transactionId": "sim-transaction"},
        },
        "NotifyChargingLimit": {},
        "NotifyCustomerInformation": {},
        "NotifyDisplayMessages": {},
        "NotifyEVChargingNeeds": {},
        "NotifyEVChargingSchedule": {},
        "NotifyEvent": {},
        "NotifyMonitoringReport": {},
        "NotifyReport": {},
        "PublishFirmwareStatusNotification": {"status": "Downloaded"},
        "ReportChargingProfiles": {},
        "ReservationStatusUpdate": {},
        "SecurityEventNotification": {},
        "SignCertificate": {"csr": "simulated-request"},
        "StatusNotification": {
            "connectorId": 1,
            "connectorStatus": "Available",
            "evseId": 1,
        },
        "TransactionEvent": {
            "eventType": "Started",
            "evse": {"connectorId": 1, "id": 1},
            "idToken": {"idToken": "sim-card"},
            "timestamp": "2026-01-01T00:00:00Z",
            "transactionInfo": {"transactionId": "sim-transaction"},
        },
    }
