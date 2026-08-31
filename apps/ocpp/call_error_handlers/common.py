"""Shared helpers for OCPP call error handlers."""

from __future__ import annotations

import json


def _json_details(details: dict | None) -> str:
    """Serialize error details into stable json when possible."""

    if not details:
        return ""
    try:
        return json.dumps(details, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(details)
