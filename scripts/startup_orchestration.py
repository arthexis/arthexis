"""Helpers for parsing startup orchestration command output."""

from __future__ import annotations

import json


def extract_payload(raw_output: str) -> dict[str, object]:
    """Return the last JSON object emitted in orchestration output."""
    lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise json.JSONDecodeError("No JSON object found in orchestration output", raw_output, 0)
