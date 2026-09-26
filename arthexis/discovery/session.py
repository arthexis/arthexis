"""Filesystem-backed evidence for charger discovery sessions."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = 1
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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


def _validated_session_id(session_id: str) -> str:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(
            "Discovery session ID must use only letters, numbers, '.', '_' or '-'."
        )
    return session_id


def _validated_artifact_path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError("Discovery artifact path must stay inside the session.")
    return candidate


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

    @property
    def lock_path(self) -> Path:
        return self.path / ".events.lock"

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

    @contextmanager
    def _event_lock(self) -> Iterator[None]:
        self.path.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

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
        artifact_path = str(_validated_artifact_path(artifact)) if artifact else None

        with self._event_lock():
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
            if artifact_path is not None:
                event["artifact"] = artifact_path

            serialized = json.dumps(event, sort_keys=True, separators=(",", ":"))
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(serialized + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return event

    def write_artifact(self, relative_path: str, content: bytes) -> dict[str, Any]:
        """Persist a larger evidence artifact and return its stable reference."""
        candidate = _validated_artifact_path(relative_path)
        destination = self.path.joinpath(*candidate.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
        return {
            "path": candidate.as_posix(),
            "sha256": sha256(content).hexdigest(),
            "bytes": len(content),
        }

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
        identifier = _validated_session_id(
            session_id
            or (created_at.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
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
        identifier = _validated_session_id(session_id)
        path = self.root / identifier
        if not path.is_dir() or not (path / "manifest.json").exists():
            raise KeyError(f"Unknown discovery session: {identifier}")
        return DiscoverySession(path=path, now=self.now)

    def list(self) -> list[str]:
        """Return known session IDs in lexical order."""
        if not self.root.exists():
            return []
        return sorted(
            path.name
            for path in self.root.iterdir()
            if path.is_dir()
            and SESSION_ID_PATTERN.fullmatch(path.name)
            and (path / "manifest.json").exists()
        )
