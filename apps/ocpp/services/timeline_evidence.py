"""Extract charger-originated timeline evidence from OCPP payloads."""

from datetime import datetime

from django.utils.dateparse import parse_datetime


def newest_event_at(action: str, payload: dict[str, object]) -> datetime | None:
    """Return the newest valid explicit charger timestamp for one OCPP call."""
    candidates: list[object] = []
    if action in {"StartTransaction", "StopTransaction", "StatusNotification", "TransactionEvent"}:
        candidates.append(payload.get("timestamp"))
    if action == "MeterValues":
        meter_values = payload.get("meterValue")
        if isinstance(meter_values, list):
            candidates.extend(
                item.get("timestamp")
                for item in meter_values
                if isinstance(item, dict)
            )
    parsed = [_parse_timestamp(value) for value in candidates]
    valid = [value for value in parsed if value is not None]
    return max(valid) if valid else None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed
