from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import time
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "arthexis-cleanup-step"
    / "scripts"
    / "turn_boundary.py"
)


def load_turn_boundary():
    spec = importlib.util.spec_from_file_location("turn_boundary", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cadence_rest_deducts_elapsed_turn_time():
    module = load_turn_boundary()
    started_at = dt.datetime(2026, 4, 25, 8, 0, tzinfo=dt.timezone.utc)
    now = started_at + dt.timedelta(seconds=215)

    rest = module.cadence_rest_seconds({"started_at": started_at.isoformat()}, 600, now=now)

    assert rest == 385


def test_cadence_rest_is_zero_after_cadence_elapsed():
    module = load_turn_boundary()
    started_at = dt.datetime(2026, 4, 25, 8, 0, tzinfo=dt.timezone.utc)
    now = started_at + dt.timedelta(seconds=601)

    rest = module.cadence_rest_seconds({"started_at": started_at.isoformat()}, 600, now=now)

    assert rest == 0


def test_cadence_rest_is_zero_when_cadence_disabled():
    module = load_turn_boundary()
    started_at = dt.datetime(2026, 4, 25, 8, 0, tzinfo=dt.timezone.utc)

    rest = module.cadence_rest_seconds({"started_at": started_at.isoformat()}, 0, now=started_at)

    assert rest == 0


def test_cadence_payload_uses_single_now_for_reporting():
    module = load_turn_boundary()
    now = dt.datetime(2026, 4, 25, 8, 10, tzinfo=dt.timezone.utc)
    state = {"started_at": (now - dt.timedelta(seconds=125)).isoformat()}

    payload = module.cadence_rest_payload(state, 600, skip_rest=False, now=now)

    assert payload["turn_elapsed_seconds_before_rest"] == 125
    assert payload["cadence_rest_seconds"] == 475
    assert payload["cadence_rest_started_at"] == now.isoformat(timespec="seconds")
    assert payload["cadence_rest_expires_at"] == (now + dt.timedelta(seconds=475)).isoformat(timespec="seconds")


def test_cleanup_step_records_cadence_expiry_without_sleeping(tmp_path, monkeypatch):
    module = load_turn_boundary()
    state_dir = tmp_path / "turn-state"
    monkeypatch.setattr(module, "STATE_DIR", state_dir)
    monkeypatch.setattr(module, "ACTIVE_STATE", state_dir / "active-turn.json")
    monkeypatch.setattr(module, "EVENT_LOG", state_dir / "events.jsonl")
    monkeypatch.setattr(module, "LOCK_PATH", state_dir / "state.lock")
    monkeypatch.setattr(module, "ARCHIVE_DIR", state_dir / "turns")
    monkeypatch.setattr(module, "CADENCE_STATE", state_dir / "cadence-rest.json")
    monkeypatch.setattr(module, "live_turn_process_identities", lambda state: {})
    monkeypatch.setattr(module, "wait_for_processes", lambda state, timeout_seconds: {})

    started_at = (dt.datetime.now(dt.timezone.utc).astimezone() - dt.timedelta(seconds=120)).isoformat(timespec="seconds")
    module.write_json(
        module.ACTIVE_STATE,
        {
            "turn_id": "turn-cadence-test",
            "label": "",
            "status": "active",
            "started_at": started_at,
            "registered_processes": [],
            "pending_end_effects": [],
            "triggered_end_effects": [],
        },
    )
    args = argparse.Namespace(timeout=0, cadence=600, skip_cadence_rest=False, force_kill=False)

    before = time.monotonic()
    result = module.cmd_cleanup_step(args)
    elapsed = time.monotonic() - before

    assert result == 0
    assert elapsed < 1
    assert not module.ACTIVE_STATE.exists()
    cadence_state = json.loads(module.CADENCE_STATE.read_text())
    assert cadence_state["turn_id"] == "turn-cadence-test"
    assert 0 < cadence_state["cadence_rest_seconds"] <= 600
    assert cadence_state["cadence_rest_started_at"]
    assert cadence_state["cadence_rest_expires_at"]
    archive = json.loads((module.ARCHIVE_DIR / "turn-cadence-test.json").read_text())
    assert archive["cleanup"]["cadence_rest_expires_at"] == cadence_state["cadence_rest_expires_at"]
