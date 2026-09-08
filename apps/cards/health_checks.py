"""Health checks owned by the cards domain."""

from __future__ import annotations

import json

from django.core.management.base import CommandError

from .reader import validate_rfid_value


def run_check_rfid(
    *,
    stdout,
    rfid_value: str | None = None,
    rfid_kind: str | None = None,
    rfid_pretty: bool = False,
    **_kwargs,
) -> None:
    """Validate a manually entered RFID value using card-reader logic."""

    if not rfid_value:
        raise CommandError("The RFID check requires --rfid-value.")
    result = validate_rfid_value(rfid_value, kind=rfid_kind)
    if "error" in result:
        raise CommandError(result["error"])
    if rfid_pretty:
        stdout.write(json.dumps(result, indent=2, sort_keys=True))
        return
    stdout.write(json.dumps(result))
