#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = BASE_DIR / ".state" / "ap_portal"


def _load_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if limit is not None:
        lines = lines[-limit:]
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _load_authorized(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip().lower() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def build_report(state_dir: Path, limit: int) -> dict[str, Any]:
    activity_path = state_dir / "activity.jsonl"
    consents_path = state_dir / "consents.jsonl"
    authorized_path = state_dir / "authorized_macs.txt"
    events = _load_jsonl(activity_path, limit=limit)
    consents = _load_jsonl(consents_path)
    authorized = _load_authorized(authorized_path)
    event_types = Counter(str(event.get("event_type") or "unknown") for event in events)
    clients: dict[str, dict[str, Any]] = {}

    for mac in authorized:
        clients[mac] = {"mac_address": mac, "authorized": True, "event_count": 0}

    for consent in consents:
        mac = str(consent.get("mac_address") or "").lower()
        if not mac:
            continue
        entry = clients.setdefault(mac, {"mac_address": mac, "event_count": 0})
        entry["authorized"] = mac in authorized
        entry["email"] = consent.get("email")
        entry["accepted_at"] = consent.get("accepted_at")
        entry["last_ip_address"] = consent.get("ip_address")

    for event in events:
        mac = str(event.get("mac_address") or "").lower()
        key = mac or str(event.get("ip_address") or "unknown")
        entry = clients.setdefault(key, {"event_count": 0})
        if mac:
            entry["mac_address"] = mac
        if event.get("ip_address"):
            entry["last_ip_address"] = event.get("ip_address")
        entry["last_event_at"] = event.get("observed_at")
        entry["last_event_type"] = event.get("event_type")
        entry["event_count"] = int(entry.get("event_count") or 0) + 1
        entry.setdefault("authorized", key in authorized)

    return {
        "state_dir": str(state_dir),
        "activity_log": str(activity_path),
        "consent_log": str(consents_path),
        "authorized_macs": str(authorized_path),
        "event_count": len(events),
        "authorized_client_count": len(authorized),
        "event_types": dict(sorted(event_types.items())),
        "clients": sorted(
            clients.values(),
            key=lambda item: str(item.get("last_event_at") or item.get("accepted_at") or ""),
            reverse=True,
        ),
    }


def print_text_report(report: dict[str, Any]) -> None:
    print(f"State: {report['state_dir']}")
    print(f"Events loaded: {report['event_count']}")
    print(f"Authorized clients: {report['authorized_client_count']}")
    if report["event_types"]:
        print("Event types:")
        for event_type, count in report["event_types"].items():
            print(f"  {event_type}: {count}")
    print("Clients:")
    for client in report["clients"]:
        mac = client.get("mac_address", "unknown")
        ip_address = client.get("last_ip_address", "")
        email = client.get("email", "")
        authorized = "authorized" if client.get("authorized") else "blocked"
        last_event = client.get("last_event_type", "")
        print(f"  {mac} {ip_address} {authorized} events={client.get('event_count', 0)} {last_event} {email}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Arthexis AP client activity logs.")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(Path(args.state_dir).expanduser().resolve(), args.limit)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
