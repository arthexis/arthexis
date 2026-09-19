"""Small reusable payload checks for OCPP 1.6 outbound action families."""


def require(payload: dict[str, object], *names: str) -> dict[str, object]:
    """Require non-empty values while preserving the action's original payload."""
    for name in names:
        value = payload.get(name)
        if value is None or value == "":
            raise ValueError(f"{name} is required")
    return payload


def optional(payload: dict[str, object]) -> dict[str, object]:
    """Accept a payload for an action whose OCPP fields are all optional."""
    return payload
