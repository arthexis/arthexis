"""High-level orchestration for one reproducible simulator transaction."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from apps.ocpp.simulator.battery import (
    DEFAULT_BATTERY_KWH,
    DEFAULT_DELIVERY_VARIATION,
    DEFAULT_LINE_VOLTAGE_V,
    DEFAULT_MAX_POWER_W,
    DEFAULT_MINIMUM_POWER_FRACTION,
    DEFAULT_TAPER_START_SOC,
    BatteryDeliveryModel,
    BatterySession,
)
from apps.ocpp.simulator.network import LiveSimulatorError
from apps.ocpp.simulator.requests import RequestJournal
from apps.ocpp.simulator.worker import send_control


SendControl = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class SingleTransactionScenario:
    """Effective parameters for one authorize/start/meter/stop session."""

    # An explicit duration preserves the original wall-clock scenario. With no
    # duration the simulator uses the battery/SOC demand model below.
    duration_seconds: float | None = None
    meter_interval_seconds: float = 30.0
    connector_id: int | None = None
    meter_start: int | None = None
    power_w: float = DEFAULT_MAX_POWER_W
    current_a: float | None = None
    voltage_v: float | None = None
    stop_reason: str | None = None
    authorization_timeout: float | None = None
    battery_kwh: float = DEFAULT_BATTERY_KWH
    start_soc: float = 0.0
    target_soc: float = 100.0
    unplug_soc: float | None = None
    taper_start_soc: float = DEFAULT_TAPER_START_SOC
    minimum_power_fraction: float = DEFAULT_MINIMUM_POWER_FRACTION
    delivery_variation: float = DEFAULT_DELIVERY_VARIATION
    line_voltage_v: float = DEFAULT_LINE_VOLTAGE_V
    seed: int = 0

    @property
    def battery_driven(self) -> bool:
        return self.duration_seconds is None

    def battery_session(self) -> BatterySession:
        return BatterySession(
            battery_kwh=self.battery_kwh,
            start_soc=self.start_soc,
            target_soc=self.target_soc,
            unplug_soc=self.unplug_soc,
            max_power_w=self.power_w,
            meter_interval_seconds=self.meter_interval_seconds,
            taper_start_soc=self.taper_start_soc,
            minimum_power_fraction=self.minimum_power_fraction,
            delivery_variation=self.delivery_variation,
            line_voltage_v=self.line_voltage_v,
            seed=self.seed,
        )

    def validate(self) -> None:
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("duration_seconds must be zero or greater")
        if self.meter_interval_seconds <= 0:
            raise ValueError("meter_interval_seconds must be greater than zero")
        if self.connector_id is not None and self.connector_id <= 0:
            raise ValueError("connector_id must be a positive integer")
        if self.meter_start is not None and self.meter_start < 0:
            raise ValueError("meter_start must be zero or greater")
        for name in ("power_w", "current_a", "voltage_v", "authorization_timeout"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be zero or greater")
        if self.battery_driven:
            self.battery_session().validate()


def _write_snapshot(
    evidence_dir: str | Path,
    *,
    charger: str,
    id_tag: str,
    scenario: SingleTransactionScenario,
    clock: dict[str, Any],
) -> tuple[str, str]:
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    run_id = uuid.uuid4().hex
    path = root / f"transaction-scenario-{run_id}.json"
    payload = {
        "run_id": run_id,
        "kind": "single-transaction",
        "charger": charger,
        "created_host_time": time.time(),
        "id_tag_sha256": RequestJournal.id_tag_fingerprint(id_tag),
        "clock": clock,
        "scenario": asdict(scenario),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return run_id, str(path)


async def _authorize(
    charger: str,
    id_tag: str,
    *,
    status: dict[str, Any],
    scenario: SingleTransactionScenario,
    run_id: str,
    send: SendControl,
) -> tuple[str, str]:
    authorization = await send(charger, {"action": "authorize", "id_tag": id_tag})
    request_id = str(authorization["request_id"])
    timeout = scenario.authorization_timeout
    if timeout is None:
        configured = status.get("authorization_timeout")
        timeout = float(configured) if configured is not None else None
    wait_request: dict[str, Any] = {
        "action": "wait-result",
        "request_id": request_id,
    }
    if timeout is not None:
        wait_request["timeout"] = timeout
    result = await send(charger, wait_request)
    if result.get("timed_out"):
        raise LiveSimulatorError(
            f"authorization timed out for scenario {run_id}: {request_id}"
        )
    if result.get("error"):
        raise LiveSimulatorError(str(result["error"]))
    return request_id, str(result.get("authorization") or "")


async def _run_duration_metering(
    charger: str,
    scenario: SingleTransactionScenario,
    connector_id: int,
    *,
    send: SendControl,
    sleep: Sleep,
) -> list[str]:
    meter_request: dict[str, Any] = {
        "action": "meter",
        "connector_id": connector_id,
        "power_w": scenario.power_w,
    }
    if scenario.current_a is not None:
        meter_request["current_a"] = scenario.current_a
    if scenario.voltage_v is not None:
        meter_request["voltage_v"] = scenario.voltage_v

    meter_ids: list[str] = []
    first_meter = await send(charger, meter_request)
    meter_ids.append(str(first_meter["request_id"]))
    elapsed = 0.0
    duration = float(scenario.duration_seconds or 0.0)
    while elapsed < duration:
        step = min(scenario.meter_interval_seconds, duration - elapsed)
        await sleep(step)
        elapsed += step
        sample = await send(
            charger,
            {"action": "meter", "connector_id": connector_id},
        )
        meter_ids.append(str(sample["request_id"]))
    return meter_ids


async def _run_battery_metering(
    charger: str,
    scenario: SingleTransactionScenario,
    connector_id: int,
    meter_start: int,
    *,
    send: SendControl,
) -> tuple[list[str], BatteryDeliveryModel, float]:
    model = BatteryDeliveryModel(scenario.battery_session())
    meter_ids: list[str] = []
    logical_elapsed = 0.0
    while not model.completed:
        chunk = model.next_chunk()
        logical_elapsed += chunk.elapsed_seconds
        sample = await send(
            charger,
            {
                "action": "meter",
                "connector_id": connector_id,
                "energy_wh": meter_start + chunk.cumulative_delivered_wh,
                "power_w": chunk.power_w,
                "current_a": chunk.current_a,
                "voltage_v": chunk.voltage_v,
            },
        )
        meter_ids.append(str(sample["request_id"]))
        # Yield so several connector sessions can progress concurrently without
        # requiring their logical charging time to pass in wall-clock time.
        await asyncio.sleep(0)
    return meter_ids, model, logical_elapsed


async def run_single_transaction_scenario(
    charger: str,
    id_tag: str,
    scenario: SingleTransactionScenario,
    *,
    send: SendControl = send_control,
    sleep: Sleep = asyncio.sleep,
) -> dict[str, Any]:
    """Run one charging session exclusively through existing worker primitives."""
    scenario.validate()
    id_tag = id_tag.strip()
    if not id_tag:
        raise ValueError("transaction run requires an idTag")

    status = await send(charger, {"action": "status"})
    evidence_dir = status.get("evidence_dir")
    if not evidence_dir:
        raise LiveSimulatorError("active simulator has no evidence directory")
    run_id, snapshot = _write_snapshot(
        evidence_dir,
        charger=charger,
        id_tag=id_tag,
        scenario=scenario,
        clock=dict(status.get("clock") or {}),
    )

    authorization_request_id, authorization_status = await _authorize(
        charger,
        id_tag,
        status=status,
        scenario=scenario,
        run_id=run_id,
        send=send,
    )
    if authorization_status != "Accepted":
        return {
            "ok": True,
            "charger": charger,
            "run_id": run_id,
            "snapshot": snapshot,
            "completed": True,
            "started": False,
            "authorization": authorization_status,
            "authorization_request_id": authorization_request_id,
            "meter_samples": 0,
        }

    start_request: dict[str, Any] = {
        "action": "transaction-start",
        "id_tag": id_tag,
    }
    if scenario.connector_id is not None:
        start_request["connector_id"] = scenario.connector_id
    if scenario.meter_start is not None:
        start_request["meter_start"] = scenario.meter_start
    started = await send(charger, start_request)
    if not started.get("started"):
        return {
            "ok": True,
            "charger": charger,
            "run_id": run_id,
            "snapshot": snapshot,
            "completed": True,
            "started": False,
            "authorization": started.get("authorization"),
            "authorization_request_id": authorization_request_id,
            "start_request_id": started.get("request_id"),
            "connector": started.get("connector"),
            "meter_samples": 0,
        }

    connector_id = int(started["connector"])
    starting_meter = int(started.get("meter_wh", scenario.meter_start or 0))
    battery_model: BatteryDeliveryModel | None = None
    logical_elapsed = scenario.duration_seconds or 0.0
    if scenario.battery_driven:
        meter_ids, battery_model, logical_elapsed = await _run_battery_metering(
            charger,
            scenario,
            connector_id,
            starting_meter,
            send=send,
        )
    else:
        meter_ids = await _run_duration_metering(
            charger,
            scenario,
            connector_id,
            send=send,
            sleep=sleep,
        )

    stop_request: dict[str, Any] = {
        "action": "transaction-stop",
        "connector_id": connector_id,
    }
    reason = scenario.stop_reason
    if reason is None and scenario.battery_driven and scenario.unplug_soc is not None:
        reason = "EVDisconnected"
    if reason:
        stop_request["reason"] = reason
    stopped = await send(charger, stop_request)

    result = {
        "ok": True,
        "charger": charger,
        "run_id": run_id,
        "snapshot": snapshot,
        "completed": True,
        "started": True,
        "connector": connector_id,
        "authorization": authorization_status,
        "authorization_request_id": authorization_request_id,
        "start_request_id": started.get("request_id"),
        "meter_request_ids": meter_ids,
        "meter_samples": len(meter_ids),
        "stop_request_id": stopped.get("request_id"),
        "transaction_id": stopped.get("transaction_id"),
        "meter_wh": stopped.get("meter_wh"),
        "duration_seconds": logical_elapsed,
    }
    if battery_model is not None:
        session = battery_model.session
        result.update(
            {
                "battery_kwh": session.battery_kwh,
                "start_soc": session.start_soc,
                "target_soc": session.target_soc,
                "unplug_soc": session.unplug_soc,
                "final_soc": battery_model.state_of_charge,
                "requested_wh": session.requested_wh,
                "delivered_wh": battery_model.delivered_wh,
                "early_unplug": session.unplug_soc is not None,
            }
        )
    return result
