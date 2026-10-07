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


def _energy_summary(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary")
    return summary if isinstance(summary, dict) else {}


def _format_power(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    watts = float(value)
    if abs(watts) >= 1000:
        return f"{watts / 1000:.1f} kW"
    return f"{watts:.0f} W"


def _format_energy(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    watt_hours = float(value)
    if abs(watt_hours) >= 1000:
        return f"{watt_hours / 1000:.1f} kWh"
    return f"{watt_hours:.0f} Wh"


def charger_view(charger: dict[str, Any], *, power_w: object = None) -> dict[str, Any]:
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
        "power": _format_power(power_w) if connected else "—",
        "detail": _connector_detail(charger) if connected else "Disconnected",
        "tone": _tone(display_status, connected=connected),
    }


def _unavailable_context() -> dict[str, Any]:
    return {
        "site_name": "Local appliance",
        "chargers": [],
        "integration_available": False,
        "energy_available": False,
        "summary": {
            "chargers": 0,
            "charging": 0,
            "site_power": "—",
            "offline": 0,
            "energy": "—",
            "transactions": 0,
        },
    }


def dashboard_context(client: LocalCliClient | None = None) -> dict[str, Any]:
    client = client or LocalCliClient()
    try:
        status_data = client.status()
    except OcppCsmsError:
        return _unavailable_context()

    raw_chargers = status_data.get("chargers")
    if not isinstance(raw_chargers, list):
        raw_chargers = []

    try:
        site_energy = _energy_summary(client.energy())
        energy_available = True
    except OcppCsmsError:
        site_energy = {}
        energy_available = False

    chargers = []
    for charger in raw_chargers:
        if not isinstance(charger, dict):
            continue
        charger_id = charger.get("id")
        power_w = None
        if energy_available and charger_id is not None and charger.get("connected"):
            try:
                power_w = _energy_summary(client.energy(charger=str(charger_id))).get(
                    "current_power_w"
                )
            except OcppCsmsError:
                power_w = None
        chargers.append(charger_view(charger, power_w=power_w))

    charging = sum(1 for charger in chargers if charger["status"] == "Charging")
    offline = sum(1 for charger in chargers if charger["status"] == "Offline")
    transactions = site_energy.get("transactions")
    if isinstance(transactions, bool) or not isinstance(transactions, int):
        transactions = 0

    return {
        "site_name": "Local appliance",
        "chargers": chargers,
        "integration_available": True,
        "energy_available": energy_available,
        "summary": {
            "chargers": len(chargers),
            "charging": charging,
            "site_power": _format_power(site_energy.get("current_power_w")),
            "offline": offline,
            "energy": _format_energy(site_energy.get("energy_wh")),
            "transactions": transactions,
        },
    }
