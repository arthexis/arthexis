"""Durable correlation evidence for asynchronous simulator requests."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


class RequestJournal:
    """Append privacy-safe request events and results to one simulator session."""

    def __init__(self, evidence_dir: str | Path | None):
        if evidence_dir:
            self.root = Path(evidence_dir)
        else:
            runtime = Path(
                os.environ.get("OCPP_SIMULATOR_RUNTIME_DIR", ".arthexis/ocpp-simulators")
            )
            self.root = runtime / "requests"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.events_path = self.root / "events.jsonl"
        self.results_path = self.root / "results.jsonl"
        self._results: dict[str, dict[str, Any]] = {}
        self._load_results()

    @staticmethod
    def new_request_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def id_tag_fingerprint(id_tag: str) -> str:
        return hashlib.sha256(id_tag.encode("utf-8")).hexdigest()

    def submitted(
        self,
        request_id: str,
        *,
        charger: str,
        id_tag: str,
        charger_time: str,
    ) -> dict[str, Any]:
        event = {
            "request_id": request_id,
            "action": "Authorize",
            "state": "submitted",
            "charger": charger,
            "charger_time": charger_time,
            "host_time": time.time(),
            "id_tag_sha256": self.id_tag_fingerprint(id_tag),
        }
        self._append(self.events_path, event)
        return event

    def completed(
        self,
        request_id: str,
        *,
        charger: str,
        authorization: str | None = None,
        error: str | None = None,
        charger_time: str,
    ) -> dict[str, Any]:
        result = {
            "request_id": request_id,
            "action": "Authorize",
            "state": "completed",
            "charger": charger,
            "charger_time": charger_time,
            "host_time": time.time(),
            "authorization": authorization,
            "error": error,
        }
        self._append(self.results_path, result)
        self._results[request_id] = result
        return result

    def result(self, request_id: str) -> dict[str, Any] | None:
        return self._results.get(request_id)

    def _load_results(self) -> None:
        if not self.results_path.exists():
            return
        for line in self.results_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            request_id = str(payload.get("request_id", ""))
            if request_id:
                self._results[request_id] = payload

    @staticmethod
    def _append(path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
        path.chmod(0o600)
