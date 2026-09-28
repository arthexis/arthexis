"""Durable filesystem session primitives for charger discovery."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EVENT_CATEGORIES = frozenset({"observation", "inference", "operator_note"})


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


@dataclass
class DiscoverySession:
    """Append durable discovery evidence to one filesystem-backed session."""

    session_id: str
    path: Path
    created_at: str
    _next_sequence: int = 1

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        session_id: str | None = None,
        created_at: datetime | None = None,
    ) -> "DiscoverySession":
        """Create a new discovery report directory and its initial event."""

        identifier = session_id or f"discovery-{uuid.uuid4().hex}"
        if not _SESSION_ID_RE.fullmatch(identifier):
            raise ValueError(
                "session_id must use only letters, digits, '.', '_' or '-' "
                "and may not contain path separators"
            )

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
        )
        session.record(
            "session_started",
            category="observation",
            timestamp=created_at,
        )
        return session

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

        with (self.path / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())

        self._next_sequence += 1
        return event
