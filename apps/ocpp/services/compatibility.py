"""Bounded durable recording of OCPP compatibility evidence."""

from collections.abc import Mapping, Sequence

from apps.ocpp.models.compatibility import CompatibilityEvidence

MAX_TEXT = 512
MAX_SEQUENCE = 16
MAX_MAPPING = 32


def bounded_value(value, *, depth=0):
    """Return a JSON-safe bounded snapshot suitable for durable evidence."""
    if depth >= 3:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, Mapping):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_MAPPING:
                result["<truncated>"] = True
                break
            result[str(key)[:128]] = bounded_value(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = [
            bounded_value(item, depth=depth + 1)
            for item in list(value)[:MAX_SEQUENCE]
        ]
        if len(value) > MAX_SEQUENCE:
            result.append("<truncated>")
        return result
    return str(value)[:MAX_TEXT]


def record_compatibility_evidence(
    *,
    kind,
    charger=None,
    charger_identity="",
    protocol="",
    unique_id="",
    action="",
    details=None,
):
    """Persist one bounded compatibility observation."""
    return CompatibilityEvidence.objects.create(
        charger=charger,
        charger_identity=charger_identity or getattr(charger, "identity", ""),
        kind=str(kind)[:80],
        protocol=str(protocol or "")[:32],
        unique_id=str(unique_id or "")[:255],
        action=str(action or "")[:120],
        details=bounded_value(details or {}),
    )
