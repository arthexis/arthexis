"""Deterministic multi-step scenario runner for the persistent OCPP simulator."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from apps.ocpp.simulator.network import LiveSimulatorError
from apps.ocpp.simulator.requests import RequestJournal
from apps.ocpp.simulator.transaction_scenario import (
    SingleTransactionScenario,
    run_single_transaction_scenario,
)
from apps.ocpp.simulator.worker import send_control

SendControl = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Sleep = Callable[[float], Awaitable[None]]

SUPPORTED_STEPS = {"transaction", "idle", "disconnect", "reconnect", "status"}
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
}


def load_scenario(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read simulator scenario: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid simulator scenario JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("simulator scenario must be a JSON object")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("simulator scenario requires a non-empty steps list")
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"scenario step {index + 1} must be an object")
        action = step.get("action")
        if action not in SUPPORTED_STEPS:
            raise ValueError(
                f"scenario step {index + 1} has unsupported action: {action!r}"
            )
        if action == "transaction":
            id_tag = str(step.get("id_tag", "")).strip()
            if not id_tag:
                raise ValueError(f"scenario step {index + 1} transaction requires id_tag")
            _transaction_scenario(step).validate()
        elif action == "idle":
            seconds = float(step.get("seconds", 0.0))
            if seconds < 0:
                raise ValueError(f"scenario step {index + 1} idle seconds must be non-negative")
    return payload


def _transaction_scenario(step: dict[str, Any]) -> SingleTransactionScenario:
    values = {name: step[name] for name in _TRANSACTION_FIELDS if name in step}
    return SingleTransactionScenario(**values)


def _sanitized_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    clean_steps: list[dict[str, Any]] = []
    for raw in payload["steps"]:
        step = dict(raw)
        id_tag = step.pop("id_tag", None)
        if id_tag is not None:
            step["id_tag_sha256"] = RequestJournal.id_tag_fingerprint(str(id_tag))
        clean_steps.append(step)
    sanitized["steps"] = clean_steps
    return sanitized


def _write_json(path: Path, payload: dict[str, Any]) -> str:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def _snapshot_run(
    evidence_dir: str | Path,
    *,
    charger: str,
    payload: dict[str, Any],
    clock: dict[str, Any],
) -> tuple[str, str, Path]:
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    run_id = uuid.uuid4().hex
    snapshot_path = root / f"scenario-run-{run_id}.json"
    snapshot = {
        "run_id": run_id,
        "kind": "multi-step",
        "charger": charger,
        "created_host_time": time.time(),
        "clock": clock,
        "scenario": _sanitized_scenario(payload),
    }
    return run_id, _write_json(snapshot_path, snapshot), root


def _result_path(root: Path, run_id: str) -> Path:
    return root / f"scenario-run-{run_id}-result.json"


async def run_scenario_sequence(
    charger: str,
    payload: dict[str, Any],
    *,
    send: SendControl = send_control,
    sleep: Sleep = asyncio.sleep,
) -> dict[str, Any]:
    """Execute a validated scenario sequentially and persist consolidated evidence."""
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("simulator scenario requires a non-empty steps list")
    # Reuse the loader's validation rules for in-memory callers.
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"scenario step {index + 1} must be an object")
        action = step.get("action")
        if action not in SUPPORTED_STEPS:
            raise ValueError(
                f"scenario step {index + 1} has unsupported action: {action!r}"
            )
        if action == "transaction":
            if not str(step.get("id_tag", "")).strip():
                raise ValueError(f"scenario step {index + 1} transaction requires id_tag")
            _transaction_scenario(step).validate()
        elif action == "idle" and float(step.get("seconds", 0.0)) < 0:
            raise ValueError(f"scenario step {index + 1} idle seconds must be non-negative")

    initial_status = await send(charger, {"action": "status"})
    evidence_dir = initial_status.get("evidence_dir")
    if not evidence_dir:
        raise LiveSimulatorError("active simulator has no evidence directory")
    run_id, snapshot, root = _snapshot_run(
        evidence_dir,
        charger=charger,
        payload=payload,
        clock=dict(initial_status.get("clock") or {}),
    )

    results: list[dict[str, Any]] = []
    try:
        for index, step in enumerate(steps, start=1):
            action = str(step["action"])
            if action == "transaction":
                child = await run_single_transaction_scenario(
                    charger,
                    str(step["id_tag"]),
                    _transaction_scenario(step),
                    send=send,
                    sleep=sleep,
                )
                result = {
                    "index": index,
                    "action": action,
                    "run_id": child.get("run_id"),
                    "completed": child.get("completed", False),
                    "started": child.get("started", False),
                    "authorization": child.get("authorization"),
                    "transaction_id": child.get("transaction_id"),
                    "meter_wh": child.get("meter_wh"),
                    "meter_samples": child.get("meter_samples", 0),
                }
            elif action == "idle":
                seconds = float(step.get("seconds", 0.0))
                await sleep(seconds)
                result = {"index": index, "action": action, "seconds": seconds}
            elif action == "disconnect":
                response = await send(charger, {"action": "disconnect"})
                result = {
                    "index": index,
                    "action": action,
                    "connected": response.get("connected", False),
                }
            elif action == "reconnect":
                response = await send(charger, {"action": "reconnect"})
                result = {
                    "index": index,
                    "action": action,
                    "connected": response.get("connected", True),
                    "boot": response.get("boot"),
                    "reconnects": response.get("reconnects"),
                }
            else:
                response = await send(charger, {"action": "status"})
                result = {
                    "index": index,
                    "action": action,
                    "connected": response.get("connected"),
                    "status": response.get("status"),
                    "transaction_id": response.get("transaction_id"),
                    "meter_wh": response.get("meter_wh"),
                    "reconnects": response.get("reconnects"),
                }
            results.append(result)
    except Exception as exc:
        failure = {
            "run_id": run_id,
            "kind": "multi-step-result",
            "charger": charger,
            "completed": False,
            "failed_step": len(results) + 1,
            "error": str(exc),
            "steps": results,
            "completed_host_time": time.time(),
        }
        result_file = _write_json(_result_path(root, run_id), failure)
        raise LiveSimulatorError(
            f"scenario {run_id} failed at step {len(results) + 1}: {exc}; evidence: {result_file}"
        ) from exc

    summary = {
        "run_id": run_id,
        "kind": "multi-step-result",
        "charger": charger,
        "completed": True,
        "steps": results,
        "completed_host_time": time.time(),
    }
    result_file = _write_json(_result_path(root, run_id), summary)
    return {
        "ok": True,
        "charger": charger,
        "run_id": run_id,
        "snapshot": snapshot,
        "result": result_file,
        "completed": True,
        "steps_completed": len(results),
        "steps": results,
    }
