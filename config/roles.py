"""Role normalization and lightweight settings validation helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured

ROLE_ALIASES: dict[str, str] = {
    "constellation": "Watchtower",
}

SUPPORTED_ROLES: tuple[str, ...] = ("Control", "Satellite", "Terminal", "Watchtower")


def normalize_role(role_name: Any) -> str:
    """Normalize role input to the canonical role identifier."""

    normalized = str(role_name or "Terminal").strip()
    if not normalized:
        return "Terminal"

    lowered = normalized.lower()
    if lowered in ROLE_ALIASES:
        return ROLE_ALIASES[lowered]

    return normalized.title()


def validate_role_settings(values: Mapping[str, Any], *, strict: bool | None = None) -> None:
    """Validate role naming only, without enforcing role-specific feature restrictions.

    Parameters:
        values: Settings mapping containing ``NODE_ROLE``.
        strict: When false, skip validation. When ``None``, strict mode follows ``DEBUG``.

    Returns:
        None

    Raises:
        ImproperlyConfigured: If ``NODE_ROLE`` is unknown.
    """

    effective_strict = not bool(values.get("DEBUG")) if strict is None else strict
    if not effective_strict:
        return

    role_name = normalize_role(values.get("NODE_ROLE", "Terminal"))
    if role_name not in SUPPORTED_ROLES:
        allowed = ", ".join(sorted(SUPPORTED_ROLES))
        raise ImproperlyConfigured(
            f"Invalid NODE_ROLE '{values.get('NODE_ROLE')}'. Supported roles: {allowed}."
        )
