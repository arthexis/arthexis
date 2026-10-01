"""Durable profile evidence for simulator sessions."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def evidence_root() -> Path:
    runtime = Path(
        os.environ.get("OCPP_SIMULATOR_RUNTIME_DIR", ".arthexis/ocpp-simulators")
    )
    root = runtime / "sessions"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def create_session_evidence(
    charger: str,
    *,
    source_profile: dict[str, Any],
    effective_profile: dict[str, Any],
) -> Path:
    """Persist source and effective profile snapshots for one simulator boot."""
    digest = hashlib.sha256(charger.encode("utf-8")).hexdigest()[:16]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = evidence_root() / f"{digest}-{stamp}-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o700)
    _write_json(path / "profile.json", source_profile)
    _write_json(path / "effective-profile.json", effective_profile)
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
