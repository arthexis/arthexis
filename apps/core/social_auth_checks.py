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
    redirect_uri: str

    @property
    def missing(self) -> list[str]:
        """Return a list of required Reddit OAuth settings that are not configured."""

        missing: list[str] = []
        if not self.client_id:
            missing.append("REDDIT_OAUTH_CLIENT_ID")
        if not self.client_secret:
            missing.append("REDDIT_OAUTH_CLIENT_SECRET")
        if not self.redirect_uri:
            missing.append("REDDIT_OAUTH_REDIRECT_URI")
        return missing


def _reddit_oauth_config() -> RedditOAuthConfig:
    """Build a Reddit OAuth config object from Django settings."""

    return RedditOAuthConfig(
        client_id=getattr(settings, "REDDIT_OAUTH_CLIENT_ID", "").strip(),
        client_secret=getattr(settings, "REDDIT_OAUTH_CLIENT_SECRET", "").strip(),
        redirect_uri=getattr(settings, "REDDIT_OAUTH_REDIRECT_URI", "").strip(),
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
