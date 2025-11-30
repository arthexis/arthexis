from __future__ import annotations

import json
from typing import Callable, Optional

from apps.core.system import (
    SystemField,
    _auto_upgrade_next_check,
    _build_system_fields,
    _gather_info,
    _resolve_auto_upgrade_namespace,
)

_SYSTEM_SIGIL_NAMESPACES: dict[str, Callable[[str], Optional[str]]] = {
    "AUTO_UPGRADE": _resolve_auto_upgrade_namespace,
}


def resolve_system_namespace_value(key: str) -> str | None:
    """Resolve dot-notation sigils mapped to dynamic ``SYS`` namespaces."""

    if not key:
        return None
    normalized_key = key.replace("-", "_").upper()
    if normalized_key == "NEXT_VER_CHECK":
        return _auto_upgrade_next_check()
    namespace, _, remainder = key.partition(".")
    if not remainder:
        return None
    normalized = namespace.replace("-", "_").upper()
    handler = _SYSTEM_SIGIL_NAMESPACES.get(normalized)
    if not handler:
        return None
    return handler(remainder)


def _export_field_value(field: SystemField) -> str:
    """Serialize a ``SystemField`` value for sigil resolution."""

    if field.field_type in {"features", "databases"}:
        return json.dumps(field.value)
    if field.field_type == "boolean":
        return "True" if field.value else "False"
    if field.value is None:
        return ""
    return str(field.value)


def get_system_sigil_values() -> dict[str, str]:
    """Expose system information in a format suitable for sigil lookups."""

    info = _gather_info()
    values: dict[str, str] = {}
    for field in _build_system_fields(info):
        exported = _export_field_value(field)
        raw_key = (field.sigil_key or "").strip()
        if not raw_key:
            continue
        variants = {
            raw_key.upper(),
            raw_key.replace("-", "_").upper(),
        }
        for variant in variants:
            values[variant] = exported
    return values
