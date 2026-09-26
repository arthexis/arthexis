"""Filesystem-backed evidence for charger discovery sessions."""

import json
import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = 1


class EvidenceKind(str, Enum):
    """Classify raw facts separately from human notes and interpretations."""

    OBSERVATION = "observation"
    NOTE = "note"
    INFERENCE = "inference"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@dataclass(frozen=True)
class DiscoverySession:
    """One append-only discovery evidence stream."""

    path: Path
    now: Callable[[], datetime] = _utc_now

    @property
    def session_id(self) -> str:
        return self.path.name

    @property
    def events_path(self) -> Path:
        return self.path / "events.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    @property
    def summary_path(self) -> Path:
        return self.path / "summary.json"

    def events(self) -> Iterator[dict[str, Any]]:
        """Yield every complete persisted event in sequence order."""
        if not self.events_path.exists():
            return
        with self.events_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    break
                stripped = line.strip()
                if stripped:
                    yield json.loads(stripped)

    def event(self, sequence: int) -> dict[str, Any]:
        """Return one persisted event by its stable sequence number."""
        for event in self.events():
            if event["seq"] == sequence:
                return event
        raise KeyError(f"{self.session_id}: no discovery event {sequence}")

    def _repair_incomplete_tail(self) -> None:
        """Discard only a partial final write left by an interrupted process."""
        if not self.events_path.exists():
            return
        with self.events_path.open("rb+") as stream:
            data = stream.read()
            if not data or data.endswith(b"\n"):
                return
            last_newline = data.rfind(b"\n")
            stream.seek(last_newline + 1 if last_newline >= 0 else 0)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())

    def append(
        self,
        event_type: str,
        *,
        kind: EvidenceKind = EvidenceKind.OBSERVATION,
        data: Mapping[str, Any] | None = None,
        artifact: str | None = None,
    ) -> dict[str, Any]:
        """Durably append one event before returning it to live consumers."""
        if not event_type:
            raise ValueError("Discovery event type is required.")

        payload = dict(data or {})
        json.dumps(payload)
        self._repair_incomplete_tail()
        current = list(self.events())
        event: dict[str, Any] = {
            "session_id": self.session_id,
            "seq": current[-1]["seq"] + 1 if current else 1,
            "time": _timestamp(self.now()),
            "type": event_type,
            "kind": kind.value,
            "data": payload,
        }
        if artifact is not None:
            event["artifact"] = artifact

        serialized = json.dumps(event, sort_keys=True, separators=(",", ":"))
        self.path.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(serialized + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def summarize(self) -> dict[str, Any]:
        """Derive a compact summary from the append-only evidence stream."""
        events = list(self.events())
        completed = next(
            (event for event in reversed(events) if event["type"] == "session_completed"),
            None,
        )
        captures = [
            event
            for event in events
            if event["type"] in {"capture_started", "capture_succeeded", "capture_failed"}
        ]
        candidates = [
            event for event in events if event["type"] == "csms_candidate"
        ]
        connections = [
            event for event in events if event["type"] == "ocpp_connection"
        ]

        summary: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "status": (
                completed["data"].get("status", "completed")
                if completed is not None
                else "active"
            ),
            "last_seq": events[-1]["seq"] if events else 0,
            "event_count": len(events),
            "csms_candidate_count": len(candidates),
            "capture_attempted": bool(captures),
            "capture_succeeded": any(
                event["type"] == "capture_succeeded" for event in captures
            ),
        }
        if candidates:
            summary["latest_candidate"] = candidates[-1]["data"]
        if connections:
            summary["charger"] = connections[-1]["data"].get("charger")
        return summary

    def write_summary(self) -> dict[str, Any]:
        """Regenerate the derived machine-readable summary atomically."""
        summary = self.summarize()
        _atomic_json(self.summary_path, summary)
        return summary

    def complete(self, *, status: str = "completed") -> dict[str, Any]:
        """Finalize the session while preserving all prior evidence."""
        self.append("session_completed", data={"status": status})
        return self.write_summary()


@dataclass(frozen=True)
class DiscoveryStore:
    """Locate and create discovery sessions under one durable root."""

    root: Path
    now: Callable[[], datetime] = _utc_now

    def create(
        self,
        *,
        interface: str | None = None,
        session_id: str | None = None,
    ) -> DiscoverySession:
        """Create a new discovery report directory and initial manifest."""
        created_at = self.now()
        identifier = session_id or (
            created_at.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        )
        path = self.root / identifier
        if path.exists():
            raise FileExistsError(f"Discovery session already exists: {identifier}")
        path.mkdir(parents=True)

        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session_id": identifier,
            "created_at": _timestamp(created_at),
        }
        if interface is not None:
            manifest["interface"] = interface
        _atomic_json(path / "manifest.json", manifest)

        session = DiscoverySession(path=path, now=self.now)
        session.append(
            "session_started",
            data={"interface": interface} if interface is not None else {},
        )
        session.write_summary()
        return session

    def open(self, session_id: str) -> DiscoverySession:
        """Open an existing discovery report without mutating it."""
        path = self.root / session_id
        if not path.is_dir() or not (path / "manifest.json").exists():
            raise KeyError(f"Unknown discovery session: {session_id}")
        return DiscoverySession(path=path, now=self.now)

    def list(self) -> list[str]:
        """Return known session IDs in lexical order."""
        if not self.root.exists():
            return []
        return sorted(
            path.name
            for path in self.root.iterdir()
            if path.is_dir() and (path / "manifest.json").exists()
        )
