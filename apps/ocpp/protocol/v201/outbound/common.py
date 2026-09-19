"""Shared OCPP 2.0.1 outbound payload validation helpers."""


def require(payload: dict[str, object], *names: str) -> dict[str, object]:
    """Return payload only when each required field is present."""
    missing = [name for name in names if name not in payload]
    if missing:
        raise ValueError(f"Missing required OCPP 2.0.1 field: {missing[0]}")
    return payload
