#!/usr/bin/env python3
"""Track Arthexis turn boundaries, end effects, and turn-owned cleanup."""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import fcntl
import json
import os
import re
import signal
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any


STATE_DIR = Path.home() / ".local" / "state" / "arthexis-turn"
ACTIVE_STATE = STATE_DIR / "active-turn.json"
EVENT_LOG = STATE_DIR / "events.jsonl"
LOCK_PATH = STATE_DIR / "state.lock"
ARCHIVE_DIR = STATE_DIR / "turns"
CADENCE_STATE = STATE_DIR / "cadence-rest.json"
WRITE_TMP_NAME = ".write-json.tmp"
DEFAULT_CLEANUP_TIMEOUT_SECONDS = 600
DEFAULT_TURN_CADENCE_SECONDS = 600
TERM_GRACE_SECONDS = 15
SAFE_TURN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_timestamp(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def turn_elapsed_seconds(state: dict[str, Any], *, now: dt.datetime | None = None) -> int:
    started_at = parse_timestamp(state.get("started_at"))
    if started_at is None:
        return 0
    current = now or dt.datetime.now(dt.timezone.utc).astimezone()
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=current.tzinfo)
    elapsed = current - started_at.astimezone(current.tzinfo)
    return max(0, int(elapsed.total_seconds()))


def cadence_rest_seconds(state: dict[str, Any], cadence_seconds: int, *, now: dt.datetime | None = None) -> int:
    cadence_seconds = max(0, int(cadence_seconds))
    if cadence_seconds <= 0:
        return 0
    return max(0, cadence_seconds - turn_elapsed_seconds(state, now=now))


def cadence_rest_payload(
    state: dict[str, Any],
    cadence_seconds: int,
    *,
    skip_rest: bool,
    now: dt.datetime,
) -> dict[str, Any]:
    cadence_seconds = max(0, int(cadence_seconds))
    elapsed_before_rest = turn_elapsed_seconds(state, now=now)
    rest_seconds = 0 if skip_rest else cadence_rest_seconds(state, cadence_seconds, now=now)
    rest_started_at = now.isoformat(timespec="seconds") if rest_seconds else ""
    rest_expires_at = (now + dt.timedelta(seconds=rest_seconds)).isoformat(timespec="seconds") if rest_seconds else ""
    return {
        "cadence_seconds": cadence_seconds,
        "turn_elapsed_seconds_before_rest": elapsed_before_rest,
        "cadence_rest_seconds": rest_seconds,
        "cadence_rest_skipped": bool(skip_rest),
        "cadence_rest_started_at": rest_started_at,
        "cadence_rest_expires_at": rest_expires_at,
    }


def write_cadence_rest_state(turn_id: str, payload: dict[str, Any]) -> None:
    if payload["cadence_rest_seconds"] <= 0:
        return
    write_json(CADENCE_STATE, {"turn_id": safe_turn_id(turn_id), **payload})


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def state_lock():
    ensure_state_dir()
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def checked_state_path(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    state_root = STATE_DIR.expanduser().resolve(strict=False)
    if not resolved.is_relative_to(state_root):
        raise ValueError(f"refusing path outside turn state directory: {path}")
    return resolved


def safe_turn_id(value: Any) -> str:
    turn_id = str(value or uuid.uuid4().hex)
    if not SAFE_TURN_ID.fullmatch(turn_id):
        raise ValueError("turn_id must contain only letters, digits, underscores, periods, or hyphens")
    return turn_id


def read_json(path: Path) -> dict[str, Any] | None:
    path = checked_state_path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return {"status": "corrupt", "path": str(path)}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_state_dir()
    path = checked_state_path(path)
    if path.parent not in {checked_state_path(STATE_DIR), checked_state_path(ARCHIVE_DIR)}:
        raise ValueError(f"refusing unsupported turn state file path: {path}")
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        fd = os.open(WRITE_TMP_NAME, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600, dir_fd=dir_fd)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(WRITE_TMP_NAME, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


def append_event(event: dict[str, Any]) -> None:
    ensure_state_dir()
    path = checked_state_path(EVENT_LOG)
    if path.parent != checked_state_path(STATE_DIR):
        raise ValueError(f"refusing unsupported event log path: {path}")
    event = {"time": now_iso(), **event}
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        fd = os.open(path.name, flags | nofollow, 0o600, dir_fd=dir_fd)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    finally:
        os.close(dir_fd)


def proc_identity(pid: int) -> dict[str, Any] | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None

    close = stat.rfind(") ")
    if close == -1:
        return None
    comm = stat[stat.find("(") + 1 : close]
    rest = stat[close + 2 :].split()
    if len(rest) < 20:
        return None
    proc_state = rest[0]
    uid = None
    for line in status.splitlines():
        if line.startswith("Uid:"):
            parts = line.split()
            if len(parts) > 1:
                uid = int(parts[1])
            break
    return {
        "pid": pid,
        "ppid": int(rest[1]),
        "comm": comm,
        "state": proc_state,
        "start_ticks": int(rest[19]),
        "uid": uid,
    }


def process_identity_is_live(identity: dict[str, Any]) -> bool:
    return identity.get("state") != "Z"


def process_matches(record: dict[str, Any]) -> bool:
    identity = proc_identity(int(record.get("pid", 0)))
    if (
        identity is None
        or identity.get("uid") != os.getuid()
        or not process_identity_is_live(identity)
    ):
        return False
    return identity.get("start_ticks") == record.get("start_ticks")


def all_process_identities() -> dict[int, dict[str, Any]]:
    identities: dict[int, dict[str, Any]] = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        identity = proc_identity(int(proc.name))
        if identity is not None:
            identities[identity["pid"]] = identity
    return identities


def descendant_pids(root_pids: set[int]) -> set[int]:
    identities = all_process_identities()
    children: dict[int, set[int]] = {}
    for pid, identity in identities.items():
        children.setdefault(identity["ppid"], set()).add(pid)
    found: set[int] = set()
    stack = list(root_pids)
    while stack:
        pid = stack.pop()
        for child in children.get(pid, set()):
            if child in found:
                continue
            found.add(child)
            stack.append(child)
    return found


def live_turn_process_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [record for record in state.get("registered_processes", []) if process_matches(record)]


def process_identity_matches(snapshot: dict[str, Any]) -> bool:
    pid = snapshot.get("pid")
    if type(pid) is not int or pid <= 1 or pid == os.getpid():
        return False
    identity = proc_identity(pid)
    if (
        identity is None
        or identity.get("uid") != os.getuid()
        or not process_identity_is_live(identity)
    ):
        return False
    return identity.get("start_ticks") == snapshot.get("start_ticks")


def live_turn_process_identities(state: dict[str, Any]) -> dict[int, dict[str, Any]]:
    roots = {int(record["pid"]) for record in live_turn_process_records(state)}
    candidates = roots | descendant_pids(roots)
    own_uid = os.getuid()
    current_pid = os.getpid()
    safe: dict[int, dict[str, Any]] = {}
    for pid in candidates:
        if pid <= 1 or pid == current_pid:
            continue
        identity = proc_identity(pid)
        if (
            identity is not None
            and identity.get("uid") == own_uid
            and process_identity_is_live(identity)
        ):
            safe[pid] = identity
    return safe


def live_turn_pids(state: dict[str, Any]) -> set[int]:
    return set(live_turn_process_identities(state))


def archive_state(state: dict[str, Any]) -> None:
    turn_id = safe_turn_id(state.get("turn_id"))
    write_json(ARCHIVE_DIR / f"{turn_id}.json", state)


def cmd_start_turn(args: argparse.Namespace) -> int:
    with state_lock():
        existing = read_json(ACTIVE_STATE)
        if existing and existing.get("status") == "active" and not args.force:
            print(f"active_turn: {existing.get('turn_id')}")
            print("status: already-active")
            return 2
        try:
            turn_id = safe_turn_id(args.turn_id or uuid.uuid4().hex)
        except ValueError as exc:
            print(f"status: invalid-turn-id {exc}")
            return 2
        state = {
            "turn_id": turn_id,
            "label": args.label or "",
            "status": "active",
            "started_at": now_iso(),
            "registered_processes": [],
            "pending_end_effects": [],
            "triggered_end_effects": [],
        }
        write_json(ACTIVE_STATE, state)
        append_event({"event": "turn-started", "turn_id": turn_id, "label": state["label"]})
    print(f"turn_started: {turn_id}")
    return 0


def cmd_register_pid(args: argparse.Namespace) -> int:
    identity = proc_identity(args.pid)
    if identity is None:
        print(f"pid_status: unavailable pid={args.pid}")
        return 1
    if identity.get("uid") != os.getuid():
        print(f"pid_status: refused non-owned pid={args.pid}")
        return 1
    with state_lock():
        state = read_json(ACTIVE_STATE)
        if not state or state.get("status") != "active":
            print("status: no-active-turn")
            return 2
        record = {
            "pid": args.pid,
            "start_ticks": identity["start_ticks"],
            "comm": identity["comm"],
            "label": args.label or "",
            "registered_at": now_iso(),
        }
        state.setdefault("registered_processes", []).append(record)
        write_json(ACTIVE_STATE, state)
        append_event({"event": "pid-registered", "turn_id": state["turn_id"], **record})
    print(f"registered_pid: {args.pid}")
    return 0


def cmd_declare_effect(args: argparse.Namespace) -> int:
    with state_lock():
        state = read_json(ACTIVE_STATE)
        if not state or state.get("status") != "active":
            print("status: no-active-turn")
            return 2
        effect = {
            "id": uuid.uuid4().hex,
            "name": args.name,
            "source": args.source or "",
            "note": args.note or "",
            "declared_at": now_iso(),
        }
        state.setdefault("pending_end_effects", []).append(effect)
        write_json(ACTIVE_STATE, state)
        append_event({"event": "end-effect-declared", "turn_id": state["turn_id"], **effect})
    print(f"declared_end_effect: {effect['id']} {effect['name']}")
    return 0


def cmd_end_step(args: argparse.Namespace) -> int:
    with state_lock():
        state = read_json(ACTIVE_STATE)
        if not state or state.get("status") != "active":
            print("arthexis-end-step")
            print("status: no-active-turn")
            print("triggered_end_effects: 0")
            return 0
        pending = state.get("pending_end_effects", [])
        triggered = []
        for effect in pending:
            next_effect = dict(effect)
            next_effect["triggered_at"] = now_iso()
            triggered.append(next_effect)
            append_event({"event": "end-effect-triggered", "turn_id": state["turn_id"], **next_effect})
        state.setdefault("triggered_end_effects", []).extend(triggered)
        state["pending_end_effects"] = []
        state["end_step_at"] = now_iso()
        write_json(ACTIVE_STATE, state)
    print("arthexis-end-step")
    print(f"turn_id: {state.get('turn_id')}")
    print(f"triggered_end_effects: {len(triggered)}")
    for effect in triggered:
        print(f"- {effect.get('name')}: {effect.get('note')}")
    if not triggered:
        print("effects: none")
    return 0


def matching_process_identities(identities: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {
        pid: identity
        for pid, identity in identities.items()
        if process_identity_matches(identity)
    }


def wait_for_processes(state: dict[str, Any], timeout_seconds: int) -> dict[int, dict[str, Any]]:
    deadline = time.monotonic() + max(0, timeout_seconds)
    candidates = live_turn_process_identities(state)
    while True:
        candidates.update(live_turn_process_identities(state))
        identities = matching_process_identities(candidates)
        if not identities or time.monotonic() >= deadline:
            return identities
        time.sleep(min(5.0, max(0.1, deadline - time.monotonic())))


def send_signal_to_verified_process(snapshot: dict[str, Any], signum: signal.Signals) -> bool:
    if not process_identity_matches(snapshot):
        return False
    pidfd_open = getattr(os, "pidfd_open", None)
    pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
    if pidfd_open is None or pidfd_send_signal is None:
        raise RuntimeError("pidfd signaling is required for turn cleanup")
    try:
        pidfd = pidfd_open(snapshot["pid"], 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        raise
    try:
        if not process_identity_matches(snapshot):
            return False
        pidfd_send_signal(pidfd, signum)
        return True
    finally:
        os.close(pidfd)


def terminate_pids(identities: dict[int, dict[str, Any]], *, force_kill: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"sigterm_sent": [], "sigkill_sent": [], "still_alive": []}
    for pid in sorted(identities, reverse=True):
        try:
            sent = send_signal_to_verified_process(identities[pid], signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            result["still_alive"].append(pid)
            continue
        if sent:
            result["sigterm_sent"].append(pid)
    if not result["sigterm_sent"]:
        return result
    time.sleep(TERM_GRACE_SECONDS)
    lingering = {pid for pid, identity in identities.items() if process_identity_matches(identity)}
    if force_kill:
        for pid in sorted(lingering, reverse=True):
            try:
                sent = send_signal_to_verified_process(identities[pid], signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                continue
            if sent:
                result["sigkill_sent"].append(pid)
        time.sleep(1)
        lingering = {pid for pid in lingering if process_identity_matches(identities[pid])}
    result["still_alive"] = sorted(lingering)
    return result


def cmd_cleanup_step(args: argparse.Namespace) -> int:
    with state_lock():
        state = read_json(ACTIVE_STATE)
    if not state or state.get("status") != "active":
        print("arthexis-cleanup-step")
        print("status: no-active-turn")
        return 0

    timeout_seconds = max(0, int(args.timeout))
    live_before = sorted(live_turn_process_identities(state))
    lingering = wait_for_processes(state, timeout_seconds)
    termination = {"sigterm_sent": [], "sigkill_sent": [], "still_alive": []}
    archive_path: Path | None = None

    cadence_payload: dict[str, Any] = {}

    with state_lock():
        state_after_wait = read_json(ACTIVE_STATE)
        if not state_after_wait or state_after_wait.get("turn_id") != state.get("turn_id"):
            print("arthexis-cleanup-step")
            print("status: turn-completed-or-changed")
            return 0
        state = state_after_wait
        lingering.update(live_turn_process_identities(state))
        lingering = matching_process_identities(lingering)
        if lingering:
            termination = terminate_pids(lingering, force_kill=args.force_kill)
        now = dt.datetime.now(dt.timezone.utc).astimezone()
        cadence_payload = cadence_rest_payload(
            state,
            args.cadence,
            skip_rest=args.skip_cadence_rest,
            now=now,
        )
        state["cleanup_step_at"] = now.isoformat(timespec="seconds")
        state["cleanup"] = {
            "timeout_seconds": timeout_seconds,
            "live_before": live_before,
            "lingering_after_wait": sorted(lingering),
            **cadence_payload,
            **termination,
        }
        if termination["still_alive"]:
            state["status"] = "active"
            write_json(ACTIVE_STATE, state)
            append_event({"event": "cleanup-incomplete", "turn_id": state["turn_id"], "cleanup": state["cleanup"]})
        else:
            state["status"] = "complete"
            write_cadence_rest_state(str(state["turn_id"]), cadence_payload)
            archive_state(state)
            archive_path = ARCHIVE_DIR / (safe_turn_id(state.get("turn_id")) + ".json")
            if ACTIVE_STATE.exists():
                ACTIVE_STATE.unlink()
            append_event({"event": "cleanup-complete", "turn_id": state["turn_id"], "cleanup": state["cleanup"]})

    print("arthexis-cleanup-step")
    print(f"turn_id: {state.get('turn_id')}")
    print(f"timeout_seconds: {timeout_seconds}")
    print(f"turn_owned_processes_at_start: {len(live_before)}")
    print(f"lingering_after_wait: {len(lingering)}")
    print(f"sigterm_sent: {len(termination['sigterm_sent'])}")
    print(f"sigkill_sent: {len(termination['sigkill_sent'])}")
    print(f"still_alive: {len(termination['still_alive'])}")
    print("cadence_seconds: {}".format(cadence_payload.get("cadence_seconds", 0)))
    print("turn_elapsed_seconds_before_rest: {}".format(cadence_payload.get("turn_elapsed_seconds_before_rest", 0)))
    print("cadence_rest_seconds: {}".format(cadence_payload.get("cadence_rest_seconds", 0)))
    print("cadence_rest_skipped: {}".format(cadence_payload.get("cadence_rest_skipped", False)))
    print("cadence_rest_expires_at: {}".format(cadence_payload.get("cadence_rest_expires_at", "")))
    if archive_path is None:
        print(f"active_state: {ACTIVE_STATE}")
    else:
        print(f"archive: {archive_path}")
    return 0 if not termination["still_alive"] else 1


def cmd_status(args: argparse.Namespace) -> int:
    with state_lock():
        state = read_json(ACTIVE_STATE)
    print("arthexis-turn-boundary")
    if not state:
        print("status: no-active-turn")
        return 0
    print(f"turn_id: {state.get('turn_id')}")
    print(f"status: {state.get('status')}")
    print(f"label: {state.get('label', '')}")
    print(f"started_at: {state.get('started_at')}")
    print(f"registered_processes: {len(state.get('registered_processes', []))}")
    print(f"live_turn_owned_processes: {len(live_turn_pids(state))}")
    print(f"pending_end_effects: {len(state.get('pending_end_effects', []))}")
    print(f"triggered_end_effects: {len(state.get('triggered_end_effects', []))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Arthexis turn boundary state.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start-turn")
    start.add_argument("--label", default="")
    start.add_argument("--turn-id", default="")
    start.add_argument("--force", action="store_true")
    start.set_defaults(func=cmd_start_turn)

    register = subparsers.add_parser("register-pid")
    register.add_argument("pid", type=int)
    register.add_argument("--label", default="")
    register.set_defaults(func=cmd_register_pid)

    effect = subparsers.add_parser("declare-effect")
    effect.add_argument("--name", required=True)
    effect.add_argument("--source", default="")
    effect.add_argument("--note", default="")
    effect.set_defaults(func=cmd_declare_effect)

    end = subparsers.add_parser("end-step")
    end.set_defaults(func=cmd_end_step)

    cleanup = subparsers.add_parser("cleanup-step")
    cleanup.add_argument("--timeout", type=int, default=DEFAULT_CLEANUP_TIMEOUT_SECONDS)
    cleanup.add_argument("--cadence", type=int, default=DEFAULT_TURN_CADENCE_SECONDS)
    cleanup.add_argument("--skip-cadence-rest", action="store_true")
    cleanup.add_argument("--force-kill", action="store_true")
    cleanup.set_defaults(func=cmd_cleanup_step)

    status = subparsers.add_parser("status")
    status.set_defaults(func=cmd_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
