"""Role-specific settings validation helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from django.core.exceptions import ImproperlyConfigured


_WATCHTOWER_REDIS_SOURCES: tuple[str, ...] = (
    "CHANNEL_REDIS_URL",
    "OCPP_STATE_REDIS_URL",
)
_WATCHTOWER_CELERY_BROKER_SETTINGS: tuple[str, ...] = ("CELERY_BROKER_URL", "BROKER_URL")
_REDIS_URL_SCHEMES = ("redis://", "rediss://")


@dataclass(frozen=True)
class RoleProfile:
    """Validation profile describing required and forbidden settings for a role."""

    name: str
    required: tuple[tuple[Callable[[Mapping[str, Any]], bool], str], ...]
    forbidden: tuple[tuple[Callable[[Mapping[str, Any]], bool], str], ...]


def _value_present(value: Any) -> bool:
    """Return whether a setting value should be treated as configured."""

    return bool(str(value).strip()) if isinstance(value, str) else bool(value)


def _watchtower_channel_backend_configured(values: Mapping[str, Any]) -> bool:
    """Return whether Watchtower has an effective Redis-backed channel backend."""

    channel_layer_decision = values.get("CHANNEL_LAYER_DECISION")
    decision_backend = str(getattr(channel_layer_decision, "backend", "")).strip().lower()
    if decision_backend:
        return "redis" in decision_backend

    if any(_value_present(values.get(setting_name)) for setting_name in _WATCHTOWER_REDIS_SOURCES):
        return True

    broker_url = ""
    for setting_name in _WATCHTOWER_CELERY_BROKER_SETTINGS:
        broker_url = str(values.get(setting_name, "")).strip().lower()
        if broker_url:
            break

    return broker_url.startswith(_REDIS_URL_SCHEMES)

ROLE_ALIASES: dict[str, str] = {
    "constellation": "Watchtower",
}


ROLE_PROFILES: dict[str, RoleProfile] = {
    "Terminal": RoleProfile(
        name="Terminal",
        required=(
            (
                lambda values: not values.get("PAGES_CHAT_ENABLED", True)
                or _value_present(values.get("PAGES_CHAT_SOCKET_PATH")),
                "PAGES_CHAT_SOCKET_PATH must be set when PAGES_CHAT_ENABLED is true.",
            ),
        ),
        forbidden=(),
    ),
    "Control": RoleProfile(
        name="Control",
        required=(
            (
                lambda values: _value_present(values.get("CELERY_BROKER_URL")),
                "CELERY_BROKER_URL is required for Control nodes.",
            ),
        ),
        forbidden=(),
    ),
    "Satellite": RoleProfile(
        name="Satellite",
        required=(
            (
                lambda values: _value_present(values.get("OCPP_STATE_REDIS_URL")),
                "OCPP_STATE_REDIS_URL is required for Satellite nodes.",
            ),
        ),
        forbidden=(),
    ),
    "Watchtower": RoleProfile(
        name="Watchtower",
        required=(
            (
                _watchtower_channel_backend_configured,
                "Watchtower requires a Redis-backed Channels configuration (CHANNEL_REDIS_URL, OCPP_STATE_REDIS_URL, CELERY_BROKER_URL, or BROKER_URL).",
            ),
        ),
        forbidden=(),
    ),
}

SUPPORTED_ROLES: tuple[str, ...] = tuple(sorted(ROLE_PROFILES))


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
    """Validate role settings and raise ``ImproperlyConfigured`` on invalid profiles."""

    effective_strict = not bool(values.get("DEBUG")) if strict is None else strict
    if not effective_strict:
        return

    role_name = normalize_role(values.get("NODE_ROLE", "Terminal"))
    profile = ROLE_PROFILES.get(role_name)
    if profile is None:
        allowed = ", ".join(sorted(ROLE_PROFILES))
        raise ImproperlyConfigured(
            f"Invalid NODE_ROLE '{values.get('NODE_ROLE')}'. Supported roles: {allowed}."
        )

    for validator, message in profile.required:
        if not validator(values):
            raise ImproperlyConfigured(f"{profile.name} role validation failed: {message}")

    for predicate, message in profile.forbidden:
        if predicate(values):
            raise ImproperlyConfigured(f"{profile.name} role validation failed: {message}")
