"""Migration-safe helpers for the OCPP app."""

import secrets


def generate_log_request_id() -> int:
    """Return a random positive identifier suitable for OCPP log requests."""

    return secrets.randbits(31) or 1
