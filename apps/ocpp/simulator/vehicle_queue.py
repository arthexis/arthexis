"""Queue vehicle charging requests across the connectors of one simulated charger."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from apps.ocpp.simulator.transaction_scenario import (
    SingleTransactionScenario,
    run_single_transaction_scenario,
)
from apps.ocpp.simulator.worker import send_control

SendControl = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

_TRANSACTION_FIELDS = {
    "duration_seconds",
    "meter_interval_seconds",
    "connector_id",
    "meter_start",
    "power_w",
    "current_a",
    "voltage_v",
    "stop_reason",
    "authorization_timeout",
    "battery_kwh",
    "start_soc",
    "target_soc",
    "unplug_soc",
    "taper_start_soc",
    "minimum_power_fraction",
    "delivery_variation",
    "line_voltage_v",
    "seed",
}


def vehicle_scenario(vehicle: dict[str, Any]) -> SingleTransactionScenario:
    values = {name: vehicle[name] for name in _TRANSACTION_FIELDS if name in vehicle}
    scenario = SingleTransactionScenario(**values)
    scenario.validate()
    return scenario


def validate_vehicle_queue(vehicles: object) -> list[dict[str, Any]]:
    if not isinstance(vehicles, list) or not vehicles:
        raise ValueError("vehicle queue requires a non-empty vehicles list")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(vehicles, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"vehicle {index} must be an object")
        id_tag = str(raw.get("id_tag", "")).strip()
        if not id_tag:
            raise ValueError(f"vehicle {index} requires id_tag")
        scenario = vehicle_scenario(raw)
        if scenario.connector_id is not None:
            raise ValueError(
                f"vehicle {index} must not pin connector_id in automatic queue mode"
            )
        normalized.append(dict(raw))
    return normalized


async def run_vehicle_queue(
    charger: str,
    vehicles: list[dict[str, Any]],
    *,
    send: SendControl = send_control,
) -> dict[str, Any]:
    """Keep available connectors occupied with queued vehicles in arrival order."""
    normalized = validate_vehicle_queue(vehicles)
    status = await send(charger, {"action": "status"})
    connector_count = int(status.get("connector_count") or 0)
    if connector_count <= 0:
        connectors = status.get("connectors")
        connector_count = len(connectors) if isinstance(connectors, list) else 1
    connector_count = max(1, connector_count)

    pending: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue()
    for index, vehicle in enumerate(normalized):
        pending.put_nowait((index, vehicle))
    results: list[dict[str, Any] | None] = [None] * len(normalized)

    async def consume(connector_id: int) -> None:
        while True:
            try:
                index, vehicle = pending.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                scenario = replace(vehicle_scenario(vehicle), connector_id=connector_id)
                result = await run_single_transaction_scenario(
                    charger,
                    str(vehicle["id_tag"]),
                    scenario,
                    send=send,
                )
                results[index] = {
                    "index": index + 1,
                    "run_id": result.get("run_id"),
                    "connector": result.get("connector"),
                    "started": result.get("started", False),
                    "completed": result.get("completed", False),
                    "authorization": result.get("authorization"),
                    "transaction_id": result.get("transaction_id"),
                    "meter_wh": result.get("meter_wh"),
                    "delivered_wh": result.get("delivered_wh"),
                    "final_soc": result.get("final_soc"),
                    "early_unplug": result.get("early_unplug", False),
                }
            finally:
                pending.task_done()

    workers = [
        asyncio.create_task(consume(connector_id))
        for connector_id in range(1, connector_count + 1)
    ]
    await asyncio.gather(*workers)
    completed = [item for item in results if item is not None]
    return {
        "ok": True,
        "charger": charger,
        "connector_count": connector_count,
        "vehicles": completed,
        "vehicles_completed": len(completed),
    }
