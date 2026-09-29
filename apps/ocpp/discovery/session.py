"""Durable filesystem session primitives for charger discovery."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from hashlib import sha256
from pathlib import PurePosixPath
from datetime import datetime, timezone
from pathlib import Path

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EVENT_CATEGORIES = frozenset({"observation", "inference", "operator_note"})
_EVENT_LOCKS: dict[str, threading.Lock] = {}
_EVENT_LOCKS_GUARD = threading.Lock()

_REQUIRED_EVENT_KEYS = frozenset(
    {
        "session_id",
        "sequence",
        "timestamp",
        "event_type",
        "category",
        "metadata",
        "artifact_refs",
    }
)


def _utc_timestamp(value: datetime | None = None) -> str:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _validated_artifact_path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError("Discovery artifact path must stay inside the session")
    return candidate


def _validate_event(
    event: object,
    *,
    session_id: str,
    expected_sequence: int,
    line_number: int,
) -> dict[str, object]:
    if not isinstance(event, dict):
        raise ValueError(
            f"events.jsonl line {line_number} must contain a JSON object"
        )
    missing = _REQUIRED_EVENT_KEYS - event.keys()
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(
            f"events.jsonl line {line_number} is missing required keys: {names}"
        )
    if event["session_id"] != session_id:
        raise ValueError(
            f"events.jsonl line {line_number} belongs to another session"
        )
    if event["sequence"] != expected_sequence:
        raise ValueError(
            f"events.jsonl line {line_number} must have sequence "
            f"{expected_sequence}"
        )
    if not isinstance(event["timestamp"], str) or not event["timestamp"]:
        raise ValueError(
            f"events.jsonl line {line_number} must have a timestamp"
        )
    if not isinstance(event["event_type"], str) or not event["event_type"].strip():
        raise ValueError(
            f"events.jsonl line {line_number} must have an event_type"
        )
    if event["category"] not in _EVENT_CATEGORIES:
        raise ValueError(
            f"events.jsonl line {line_number} has an invalid category"
        )
    if not isinstance(event["metadata"], dict):
        raise ValueError(
            f"events.jsonl line {line_number} metadata must be an object"
        )
    artifact_refs = event["artifact_refs"]
    if not isinstance(artifact_refs, list) or not all(
        isinstance(reference, str) for reference in artifact_refs
    ):
        raise ValueError(
            f"events.jsonl line {line_number} artifact_refs must be strings"
        )
    return event


@dataclass
class DiscoverySession:
    """Append and inspect durable OCPP discovery evidence."""

    session_id: str
    path: Path
    created_at: str
    _next_sequence: int = 1
    projector: Callable[[Mapping[str, object]], object] | None = None

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        session_id: str | None = None,
        created_at: datetime | None = None,
        projector: Callable[[Mapping[str, object]], object] | None = None,
    ) -> DiscoverySession:
        """Create a new discovery report directory and its initial event."""

        identifier = (
            session_id
            if session_id is not None
            else f"discovery-{uuid.uuid4().hex}"
        )
        cls._validate_session_id(identifier)

        reports_root = Path(root) / "discovery"
        path = reports_root / identifier
        path.mkdir(parents=True, exist_ok=False)
        for directory in ("network", "traffic", "ocpp", "notes"):
            (path / directory).mkdir()

        timestamp = _utc_timestamp(created_at)
        manifest = {
            "format_version": 1,
            "session_id": identifier,
            "created_at": timestamp,
            "status": "active",
        }
        _write_json(path / "manifest.json", manifest)
        (path / "events.jsonl").touch()

        session = cls(
            session_id=identifier,
            path=path,
            created_at=timestamp,
            projector=projector,
        )
        session.record(
            "session_started",
            category="observation",
            timestamp=created_at,
        )
        session.write_summary()
        return session

    @classmethod
    def open(
        cls,
        root: Path,
        session_id: str,
        *,
        projector: Callable[[Mapping[str, object]], object] | None = None,
    ) -> DiscoverySession:
        """Open an existing session, repairing only an interrupted final append."""

        cls._validate_session_id(session_id)
        path = Path(root) / "discovery" / session_id
        manifest = _read_json(path / "manifest.json")
        if manifest.get("session_id") != session_id:
            raise ValueError("manifest session_id does not match report directory")
        created_at = manifest.get("created_at")
        if not isinstance(created_at, str) or not created_at:
            raise ValueError("manifest created_at must be a non-empty string")

        events = cls._load_events(
            path / "events.jsonl",
            session_id=session_id,
            repair_truncated_tail=True,
        )
        return cls(
            session_id=session_id,
            path=path,
            created_at=created_at,
            _next_sequence=len(events) + 1,
            projector=projector,
        )

    @property
    def handoff_path(self) -> Path:
        return self.path / "handoff.json"

    @property
    def claimed_handoff_path(self) -> Path:
        return self.path / "handoff.claimed.json"

    def _thread_lock(self) -> threading.Lock:
        key = str(self.path.resolve())
        with _EVENT_LOCKS_GUARD:
            return _EVENT_LOCKS.setdefault(key, threading.Lock())

    @contextmanager
    def _event_lock(self):
        self.path.mkdir(parents=True, exist_ok=True)
        with self._thread_lock():
            with (self.path / ".events.lock").open("a+b") as stream:
                os.lockf(stream.fileno(), os.F_LOCK, 0)
                try:
                    yield
                finally:
                    os.lockf(stream.fileno(), os.F_ULOCK, 0)

    def write_artifact(self, relative_path: str, content: bytes) -> dict[str, object]:
        """Persist a larger discovery artifact inside the session."""
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

    def arm_handoff(
        self,
        *,
        charger_identity: str | None = None,
        client_host: str | None = None,
        original_destination: Mapping[str, object] | None = None,
        strategy: str | None = None,
    ) -> dict[str, object]:
        """Persist the expected redirected connection before network mutation."""
        if not charger_identity and not client_host:
            raise ValueError(
                "Discovery handoff requires charger_identity and/or client_host"
            )
        payload: dict[str, object] = {
            "charger_identity": charger_identity or "",
            "client_host": client_host or "",
        }
        if original_destination is not None:
            payload["original_destination"] = dict(original_destination)
        if strategy:
            payload["strategy"] = strategy
        with self._event_lock():
            if self.handoff_path.exists() or self.claimed_handoff_path.exists():
                raise FileExistsError(
                    f"{self.session_id}: discovery handoff is already armed or claimed"
                )
            _write_json(self.handoff_path, payload)
        self.record("handoff_armed", metadata=payload)
        self.write_summary()
        return payload

    def claim_handoff(
        self,
        *,
        charger_identity: str,
        client_host: str | None = None,
    ) -> dict[str, object] | None:
        """Atomically claim this session for a matching OCPP connection."""
        with self._event_lock():
            if not self.handoff_path.exists():
                return None
            payload = _read_json(self.handoff_path)
            expected_identity = str(payload.get("charger_identity") or "")
            expected_host = str(payload.get("client_host") or "")
            if expected_identity and expected_identity != charger_identity:
                return None
            if expected_host and expected_host != (client_host or ""):
                return None
            self.handoff_path.replace(self.claimed_handoff_path)
            return payload

    @classmethod
    def list(cls, root: Path) -> list[dict[str, object]]:
        """List valid discovery sessions ordered by creation time then ID."""

        reports_root = Path(root) / "discovery"
        if not reports_root.exists():
            return []

        sessions: list[dict[str, object]] = []
        for path in reports_root.iterdir():
            if not path.is_dir() or not _SESSION_ID_RE.fullmatch(path.name):
                continue
            manifest_path = path / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = _read_json(manifest_path)
            if manifest.get("session_id") != path.name:
                continue
            sessions.append(
                {
                    "session_id": path.name,
                    "created_at": manifest.get("created_at"),
                    "status": manifest.get("status"),
                }
            )

        return sorted(
            sessions,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item["session_id"]),
            ),
        )

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError(
                "session_id must use only letters, digits, '.', '_' or '-' "
                "and may not contain path separators"
            )

    @staticmethod
    def _load_events(
        path: Path,
        *,
        session_id: str,
        repair_truncated_tail: bool = False,
    ) -> list[dict[str, object]]:
        raw = path.read_bytes()
        lines = raw.splitlines(keepends=True)
        events: list[dict[str, object]] = []
        committed_bytes = 0

        for index, raw_line in enumerate(lines):
            line_number = index + 1
            is_final = index == len(lines) - 1
            has_newline = raw_line.endswith((b"\n", b"\r"))

            try:
                text = raw_line.decode("utf-8")
                if not text.strip():
                    committed_bytes += len(raw_line)
                    continue
                payload = json.loads(text)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if is_final and not has_newline:
                    if repair_truncated_tail:
                        with path.open("r+b") as stream:
                            stream.truncate(committed_bytes)
                            stream.flush()
                            os.fsync(stream.fileno())
                    break
                raise ValueError(
                    f"events.jsonl line {line_number} is corrupt"
                ) from exc

            event = _validate_event(
                payload,
                session_id=session_id,
                expected_sequence=len(events) + 1,
                line_number=line_number,
            )
            events.append(event)
            committed_bytes += len(raw_line)

        return events

    def events(self) -> list[dict[str, object]]:
        """Return all valid committed events in authoritative stream order."""

        return self._load_events(
            self.path / "events.jsonl",
            session_id=self.session_id,
        )

    def summary(self) -> dict[str, object]:
        """Derive a machine-readable summary from manifest and event evidence."""

        manifest = _read_json(self.path / "manifest.json")
        events = self.events()

        event_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        capture_requested = False
        capture_available: bool | None = None
        capture_attempted = False
        capture_succeeded = False
        capture_strategy: str | None = None
        capture_failure_reason: str | None = None
        capture_redirect_id: str | None = None
        capture_active = False
        capture_release_failure_reason: str | None = None
        charger: dict[str, object] | None = None
        for event in events:
            event_type = event["event_type"]
            category = event["category"]
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1

            metadata = event["metadata"]
            if event_type == "capture_requested":
                capture_requested = True
            elif event_type == "capture_available":
                capture_available = True
                strategy = metadata.get("strategy")
                if isinstance(strategy, str):
                    capture_strategy = strategy
            elif event_type == "capture_unavailable":
                capture_available = False
                reason = metadata.get("reason")
                if isinstance(reason, str):
                    capture_failure_reason = reason
            elif event_type in {"capture_started", "capture_failed", "capture_succeeded"}:
                capture_attempted = True
                if event_type == "capture_started":
                    redirect_id = metadata.get("redirect_id")
                    if isinstance(redirect_id, str):
                        capture_redirect_id = redirect_id
                    capture_active = True
                elif event_type == "capture_failed":
                    reason = metadata.get("reason")
                    if isinstance(reason, str):
                        capture_failure_reason = reason
                elif event_type == "capture_succeeded":
                    capture_succeeded = True
            elif event_type == "capture_released":
                capture_active = False
                capture_release_failure_reason = None
            elif event_type == "capture_release_failed":
                reason = metadata.get("reason")
                if isinstance(reason, str):
                    capture_release_failure_reason = reason
            elif event_type == "ocpp_connection":
                selected = metadata.get("charger")
                if isinstance(selected, dict):
                    charger = dict(selected)

        capture = {
            "requested": capture_requested,
            "available": capture_available if capture_requested else None,
            "attempted": capture_attempted,
            "succeeded": capture_succeeded,
            "strategy": capture_strategy,
            "failure_reason": capture_failure_reason,
            "redirect_id": capture_redirect_id,
            "active": capture_active,
            "release_failure_reason": capture_release_failure_reason,
        }

        return {
            "format_version": 1,
            "session_id": self.session_id,
            "created_at": manifest.get("created_at"),
            "status": manifest.get("status"),
            "event_count": len(events),
            "event_counts": event_counts,
            "category_counts": category_counts,
            "last_sequence": events[-1]["sequence"] if events else None,
            "last_event_at": events[-1]["timestamp"] if events else None,
            "capture": capture,
            "handoff_armed": self.handoff_path.exists(),
            "handoff_claimed": self.claimed_handoff_path.exists(),
            "charger": charger,
        }

    def write_summary(self) -> dict[str, object]:
        """Regenerate summary.json from the authoritative event stream."""

        summary = self.summary()
        _write_json(self.path / "summary.json", summary)
        return summary

    def record(
        self,
        event_type: str,
        *,
        category: str = "observation",
        metadata: Mapping[str, object] | None = None,
        artifact_refs: Iterable[str] = (),
        timestamp: datetime | None = None,
    ) -> dict[str, object]:
        """Append one chronological event and flush it to durable storage."""

        if not event_type or not event_type.strip():
            raise ValueError("event_type must be non-empty")
        if category not in _EVENT_CATEGORIES:
            allowed = ", ".join(sorted(_EVENT_CATEGORIES))
            raise ValueError(f"category must be one of: {allowed}")

        event: dict[str, object] = {
            "session_id": self.session_id,
            "sequence": self._next_sequence,
            "timestamp": _utc_timestamp(timestamp),
            "event_type": event_type,
            "category": category,
            "metadata": dict(metadata or {}),
            "artifact_refs": list(artifact_refs),
        }
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"

        with self._event_lock():
            current = self._load_events(
                self.path / "events.jsonl",
                session_id=self.session_id,
                repair_truncated_tail=True,
            )
            event["sequence"] = len(current) + 1
            encoded = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            with (self.path / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._next_sequence = event["sequence"] + 1

        if self.projector is not None:
            try:
                self.projector(event)
            except Exception:
                pass
        return event
