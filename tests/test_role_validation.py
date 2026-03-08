"""Tests for role settings validation profiles."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.roles import validate_role_settings


@pytest.mark.parametrize(
    "settings_values",
    [
        {
            "DEBUG": False,
            "NODE_ROLE": "Terminal",
            "PAGES_CHAT_ENABLED": True,
            "PAGES_CHAT_SOCKET_PATH": "/ws/pages/chat/",
        },
        {
            "DEBUG": False,
            "NODE_ROLE": "Control",
            "CELERY_BROKER_URL": "redis://localhost:6379/0",
        },
        {
            "DEBUG": False,
            "NODE_ROLE": "Satellite",
            "OCPP_STATE_REDIS_URL": "redis://localhost:6379/1",
        },
        {
            "DEBUG": False,
            "NODE_ROLE": "Watchtower",
            "CHANNEL_REDIS_URL": "redis://localhost:6379/2",
        },
        {
            "DEBUG": False,
            "NODE_ROLE": "Watchtower",
            "CELERY_BROKER_URL": "redis://localhost:6379/3",
        },
        {
            "DEBUG": False,
            "NODE_ROLE": "Watchtower",
            "BROKER_URL": "redis://localhost:6379/4",
        },
    ],
)
def test_role_profiles_accept_valid_configuration(settings_values: dict[str, object]) -> None:
    """Each role profile accepts a complete valid configuration."""

    validate_role_settings(settings_values)


@pytest.mark.parametrize(
    ("settings_values", "expected_message"),
    [
        (
            {
                "DEBUG": False,
                "NODE_ROLE": "Terminal",
                "PAGES_CHAT_ENABLED": True,
                "PAGES_CHAT_SOCKET_PATH": "",
            },
            "Terminal role validation failed",
        ),
        (
            {
                "DEBUG": False,
                "NODE_ROLE": "Control",
                "CELERY_BROKER_URL": "",
            },
            "Control role validation failed",
        ),
        (
            {
                "DEBUG": False,
                "NODE_ROLE": "Satellite",
                "OCPP_STATE_REDIS_URL": "",
            },
            "Satellite role validation failed",
        ),
        (
            {
                "DEBUG": False,
                "NODE_ROLE": "Watchtower",
                "CHANNEL_REDIS_URL": "",
                "OCPP_STATE_REDIS_URL": "",
                "CELERY_BROKER_URL": "memory://localhost/",
            },
            "Watchtower role validation failed",
        ),
    ],
)
def test_role_profiles_reject_invalid_configuration(
    settings_values: dict[str, object], expected_message: str
) -> None:
    """Each role profile rejects one invalid required or forbidden combination."""

    with pytest.raises(ImproperlyConfigured, match=expected_message):
        validate_role_settings(settings_values)


def test_role_validation_is_relaxed_in_debug_mode() -> None:
    """Development defaults skip strict role validation while DEBUG is enabled."""

    validate_role_settings(
        {
            "DEBUG": True,
            "NODE_ROLE": "Control",
            "CELERY_BROKER_URL": "",
        }
    )


def test_unknown_role_raises_specific_configuration_error() -> None:
    """Unknown roles are rejected when strict validation is active."""

    with pytest.raises(ImproperlyConfigured, match="Invalid NODE_ROLE"):
        validate_role_settings({"DEBUG": False, "NODE_ROLE": "UnknownRole"})
