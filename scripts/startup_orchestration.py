"""Helpers for parsing startup orchestration command output."""

from __future__ import annotations

import json


def extract_payload(raw_output: str) -> dict[str, object]:
    """Return the last JSON object emitted in orchestration output."""
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        return payload

    lines = raw_output.splitlines()
    for start in range(len(lines) - 1, -1, -1):
        candidate = "\n".join(lines[start:]).strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    for line in reversed(raw_output.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise json.JSONDecodeError("No JSON object found in orchestration output", raw_output, 0)
