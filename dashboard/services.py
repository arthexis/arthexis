from __future__ import annotations

from typing import Any

from integrations.ocpp_csms import LocalCliClient, OcppCsmsError


def _tone(status: str, *, connected: bool) -> str:
    if not connected:
        return "rose"
    if status == "Charging":
        return "emerald"
    if status in {"Available", "Preparing", "Finishing", "SuspendedEV", "SuspendedEVSE"}:
        return "sky"
    if status in {"Faulted", "Unavailable"}:
        return "rose"
    return "sky"


def _connector_detail(charger: dict[str, Any]) -> str:
    connectors = charger.get("connectors")
    if not isinstance(connectors, list) or not connectors:
        return "No connector state"

    active = next(
        (
            connector
            for connector in connectors
            if isinstance(connector, dict) and connector.get("status") == "Charging"
        ),
        None,
    )
    connector = active or next(
        (item for item in connectors if isinstance(item, dict)),
        None,
    )
    if connector is None:
        return "No connector state"

    connector_id = connector.get("id")
    if connector_id is None:
        return "Connector"
    return f"Connector {connector_id}"


def charger_view(charger: dict[str, Any]) -> dict[str, Any]:
    connected = bool(charger.get("connected"))
    status = charger.get("status")
    if not connected:
        display_status = "Offline"
    elif isinstance(status, str) and status:
        display_status = status
    else:
        display_status = "Connected"

    charger_id = charger.get("id")
    return {
        "name": str(charger_id) if charger_id is not None else "Unknown",
        "status": display_status,
        "power": "—",
        "detail": _connector_detail(charger) if connected else "Disconnected",
        "tone": _tone(display_status, connected=connected),
    }


def dashboard_context(client: LocalCliClient | None = None) -> dict[str, Any]:
    client = client or LocalCliClient()
    try:
        status_data = client.status()
    except OcppCsmsError:
        return {
            "site_name": "Local appliance",
            "chargers": [],
            "integration_available": False,
            "summary": {
                "chargers": 0,
                "charging": 0,
                "site_power": "—",
                "offline": 0,
            },
        }

    raw_chargers = status_data.get("chargers")
    if not isinstance(raw_chargers, list):
        raw_chargers = []

    chargers = [
        charger_view(charger)
        for charger in raw_chargers
        if isinstance(charger, dict)
    ]
    charging = sum(1 for charger in chargers if charger["status"] == "Charging")
    offline = sum(1 for charger in chargers if charger["status"] == "Offline")

    return {
        "site_name": "Local appliance",
        "chargers": chargers,
        "integration_available": True,
        "summary": {
            "chargers": len(chargers),
            "charging": charging,
            "site_power": "—",
            "offline": offline,
        },
    }
