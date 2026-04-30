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


def configure_state_dir(module, state_dir: Path, monkeypatch):
    monkeypatch.setattr(module, "STATE_DIR", state_dir)
    monkeypatch.setattr(module, "ACTIVE_STATE", state_dir / "active-turn.json")
    monkeypatch.setattr(module, "EVENT_LOG", state_dir / "events.jsonl")
    monkeypatch.setattr(module, "LOCK_PATH", state_dir / "state.lock")
    monkeypatch.setattr(module, "ARCHIVE_DIR", state_dir / "turns")
    monkeypatch.setattr(module, "CADENCE_STATE", state_dir / "cadence-rest.json")


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
    configure_state_dir(module, state_dir, monkeypatch)
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


def test_cleanup_step_clears_stale_cadence_state_when_rest_is_skipped(tmp_path, monkeypatch):
    module = load_turn_boundary()
    state_dir = tmp_path / "turn-state"
    configure_state_dir(module, state_dir, monkeypatch)
    monkeypatch.setattr(module, "live_turn_process_identities", lambda state: {})
    monkeypatch.setattr(module, "wait_for_processes", lambda state, timeout_seconds: {})

    module.write_json(module.CADENCE_STATE, {"turn_id": "old-turn", "cadence_rest_seconds": 500})
    module.write_json(
        module.ACTIVE_STATE,
        {
            "turn_id": "skip-cadence-test",
            "label": "",
            "status": "active",
            "started_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
            "registered_processes": [],
            "pending_end_effects": [],
            "triggered_end_effects": [],
        },
    )
    args = argparse.Namespace(timeout=0, cadence=600, skip_cadence_rest=True, force_kill=False)

    assert module.cmd_cleanup_step(args) == 0

    assert not module.CADENCE_STATE.exists()
    archive = json.loads((module.ARCHIVE_DIR / "skip-cadence-test.json").read_text())
    assert archive["cleanup"]["cadence_rest_seconds"] == 0
    assert archive["cleanup"]["cadence_rest_skipped"] is True


def test_cleanup_step_clears_stale_cadence_state_when_cadence_already_elapsed(tmp_path, monkeypatch):
    module = load_turn_boundary()
    state_dir = tmp_path / "turn-state"
    configure_state_dir(module, state_dir, monkeypatch)
    monkeypatch.setattr(module, "live_turn_process_identities", lambda state: {})
    monkeypatch.setattr(module, "wait_for_processes", lambda state, timeout_seconds: {})

    module.write_json(module.CADENCE_STATE, {"turn_id": "old-turn", "cadence_rest_seconds": 500})
    started_at = (dt.datetime.now(dt.timezone.utc).astimezone() - dt.timedelta(seconds=601)).isoformat(timespec="seconds")
    module.write_json(
        module.ACTIVE_STATE,
        {
            "turn_id": "elapsed-cadence-test",
            "label": "",
            "status": "active",
            "started_at": started_at,
            "registered_processes": [],
            "pending_end_effects": [],
            "triggered_end_effects": [],
        },
    )
    args = argparse.Namespace(timeout=0, cadence=600, skip_cadence_rest=False, force_kill=False)

    assert module.cmd_cleanup_step(args) == 0

    assert not module.CADENCE_STATE.exists()
    archive = json.loads((module.ARCHIVE_DIR / "elapsed-cadence-test.json").read_text())
    assert archive["cleanup"]["cadence_rest_seconds"] == 0
    assert archive["cleanup"]["cadence_rest_skipped"] is False


def test_state_lock_uses_msvcrt_when_fcntl_is_unavailable(tmp_path, monkeypatch):
    module = load_turn_boundary()
    configure_state_dir(module, tmp_path / "turn-state", monkeypatch)
    calls = []

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        def locking(self, fileno, mode, size):
            calls.append((mode, size))

    monkeypatch.setattr(module, "_HAS_FCNTL", False)
    monkeypatch.setattr(module, "_HAS_MSVCRT", True)
    monkeypatch.setattr(module, "msvcrt", FakeMsvcrt())

    with module.state_lock():
        assert module.LOCK_PATH.exists()

    assert calls == [(1, 1), (2, 1)]


def test_windows_path_based_state_io_writes_json_and_events(tmp_path, monkeypatch):
    module = load_turn_boundary()
    configure_state_dir(module, tmp_path / "turn-state", monkeypatch)
    monkeypatch.setattr(module.os, "name", "nt")

    module.write_json(module.ACTIVE_STATE, {"turn_id": "windows-path", "status": "active"})
    module.append_event({"event": "windows-event", "turn_id": "windows-path"})

    assert json.loads(module.ACTIVE_STATE.read_text())["turn_id"] == "windows-path"
    assert "windows-event" in module.EVENT_LOG.read_text()


def test_windows_live_turn_process_identities_preserve_registered_pids(monkeypatch):
    module = load_turn_boundary()
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module, "_is_process_running", lambda pid: pid == 4242)

    identities = module.live_turn_process_identities(
        {
            "registered_processes": [
                {"pid": 4242, "label": "worker", "registered_at": "now"},
                {"pid": 7777, "label": "stale", "registered_at": "now"},
            ]
        }
    )

    assert identities == {
        4242: {
            "pid": 4242,
            "label": "worker",
            "registered_at": "now",
            "platform": "windows",
        }
    }
    assert module.matching_process_identities(identities) == identities
    assert module.terminate_pids(identities, force_kill=True)["still_alive"] == [4242]


def test_cleanup_step_keeps_windows_registered_pids_active(tmp_path, monkeypatch):
    module = load_turn_boundary()
    configure_state_dir(module, tmp_path / "turn-state", monkeypatch)
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module, "_is_process_running", lambda pid: pid == 4242)
    module.write_json(
        module.ACTIVE_STATE,
        {
            "turn_id": "windows-cleanup",
            "label": "",
            "status": "active",
            "started_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
            "registered_processes": [{"pid": 4242, "label": "worker", "registered_at": "now"}],
            "pending_end_effects": [],
            "triggered_end_effects": [],
        },
    )
    args = argparse.Namespace(timeout=0, cadence=0, skip_cadence_rest=True, force_kill=False)

    assert module.cmd_cleanup_step(args) == 1

    state = json.loads(module.ACTIVE_STATE.read_text())
    assert state["status"] == "active"
    assert state["cleanup"]["still_alive"] == [4242]
