"""High-level orchestration for one reproducible simulator transaction."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from apps.ocpp.simulator.network import LiveSimulatorError
from apps.ocpp.simulator.requests import RequestJournal
from apps.ocpp.simulator.worker import send_control


SendControl = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class SingleTransactionScenario:
    """Effective parameters for one authorize/start/meter/stop session."""

    duration_seconds: float = 60.0
    meter_interval_seconds: float = 30.0
    connector_id: int = 1
    meter_start: int | None = None
    power_w: float = 0.0
    current_a: float | None = None
    voltage_v: float | None = None
    stop_reason: str | None = None
    authorization_timeout: float | None = None

    def validate(self) -> None:
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must be zero or greater")
        if self.meter_interval_seconds <= 0:
            raise ValueError("meter_interval_seconds must be greater than zero")
        if self.connector_id != 1:
            raise ValueError("simulator currently supports connector 1 only")
        if self.meter_start is not None and self.meter_start < 0:
            raise ValueError("meter_start must be zero or greater")
        for name in ("power_w", "current_a", "voltage_v", "authorization_timeout"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be zero or greater")


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

    authorization = await send(charger, {"action": "authorize", "id_tag": id_tag})
    authorization_request_id = str(authorization["request_id"])
    timeout = scenario.authorization_timeout
    if timeout is None:
        configured = status.get("authorization_timeout")
        timeout = float(configured) if configured is not None else None
    wait_request: dict[str, Any] = {
        "action": "wait-result",
        "request_id": authorization_request_id,
    }
    if timeout is not None:
        wait_request["timeout"] = timeout
    authorization_result = await send(charger, wait_request)
    if authorization_result.get("timed_out"):
        raise LiveSimulatorError(
            f"authorization timed out for scenario {run_id}: {authorization_request_id}"
        )
    if authorization_result.get("error"):
        raise LiveSimulatorError(str(authorization_result["error"]))
    authorization_status = authorization_result.get("authorization")
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
        "connector_id": scenario.connector_id,
    }
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
            "meter_samples": 0,
        }

    meter_request: dict[str, Any] = {
        "action": "meter",
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
    while elapsed < scenario.duration_seconds:
        step = min(scenario.meter_interval_seconds, scenario.duration_seconds - elapsed)
        await sleep(step)
        elapsed += step
        sample = await send(charger, {"action": "meter"})
        meter_ids.append(str(sample["request_id"]))

    stop_request: dict[str, Any] = {"action": "transaction-stop"}
    if scenario.stop_reason:
        stop_request["reason"] = scenario.stop_reason
    stopped = await send(charger, stop_request)

    return {
        "ok": True,
        "charger": charger,
        "run_id": run_id,
        "snapshot": snapshot,
        "completed": True,
        "started": True,
        "authorization": authorization_status,
        "authorization_request_id": authorization_request_id,
        "start_request_id": started.get("request_id"),
        "meter_request_ids": meter_ids,
        "meter_samples": len(meter_ids),
        "stop_request_id": stopped.get("request_id"),
        "transaction_id": stopped.get("transaction_id"),
        "meter_wh": stopped.get("meter_wh"),
        "duration_seconds": scenario.duration_seconds,
    }
