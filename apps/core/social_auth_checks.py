"""System checks and startup guards for social authentication providers."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured


class RedditOAuthConfigurationError(ImproperlyConfigured):
    """Raised when Reddit OAuth is enabled without the required secrets."""


@dataclass(frozen=True)
class RedditOAuthConfig:
    """Represent required Reddit OAuth settings values."""

    client_id: str
    client_secret: str

    @property
    def missing(self) -> list[str]:
        """Return a list of required Reddit OAuth settings that are not configured."""

        required_settings = {
            "client_id": "REDDIT_OAUTH_CLIENT_ID",
            "client_secret": "REDDIT_OAUTH_CLIENT_SECRET",
        }
        missing = []
        for field, setting_name in required_settings.items():
            if not getattr(self, field):
                missing.append(setting_name)
        return missing


def _reddit_oauth_config() -> RedditOAuthConfig:
    """Build a Reddit OAuth config object from Django settings."""

    return RedditOAuthConfig(
        client_id=getattr(settings, "REDDIT_OAUTH_CLIENT_ID", "").strip(),
        client_secret=getattr(settings, "REDDIT_OAUTH_CLIENT_SECRET", "").strip(),
    )


def validate_reddit_oauth_settings() -> None:
    """Raise a specific exception when Reddit auth is enabled but incomplete."""

    if not getattr(settings, "REDDIT_AUTH_ENABLED", False):
        return

    config = _reddit_oauth_config()
    missing = config.missing
    if not missing:
        return

    raise RedditOAuthConfigurationError(
        "Reddit OAuth is enabled but missing required settings: "
        + ", ".join(missing)
    )


@register()
def check_reddit_oauth_settings(app_configs=None, **kwargs):
    """Report Reddit OAuth configuration problems to Django's check framework."""

    try:
        validate_reddit_oauth_settings()
    except RedditOAuthConfigurationError as exc:
        return [
            Error(
                str(exc),
                id="core.E001",
            )
        ]
    return []
