"""Regression tests for Reddit OAuth social-auth configuration checks."""

from django.test import override_settings

from apps.core.social_auth_checks import (
    RedditOAuthConfigurationError,
    check_reddit_oauth_settings,
    validate_reddit_oauth_settings,
)


@override_settings(REDDIT_AUTH_ENABLED=False)
def test_reddit_oauth_validation_is_noop_when_disabled():
    """Reddit OAuth validation should pass when the feature flag is disabled."""

    validate_reddit_oauth_settings()


@override_settings(
    REDDIT_AUTH_ENABLED=True,
    REDDIT_OAUTH_CLIENT_ID="",
    REDDIT_OAUTH_CLIENT_SECRET="",
    REDDIT_OAUTH_REDIRECT_URI="",
)
def test_reddit_oauth_validation_raises_specific_exception_for_missing_values():
    """Missing Reddit OAuth settings should raise RedditOAuthConfigurationError."""

    try:
        validate_reddit_oauth_settings()
    except RedditOAuthConfigurationError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected RedditOAuthConfigurationError to be raised")

    assert "REDDIT_OAUTH_CLIENT_ID" in message
    assert "REDDIT_OAUTH_CLIENT_SECRET" in message
    assert "REDDIT_OAUTH_REDIRECT_URI" in message


@override_settings(
    REDDIT_AUTH_ENABLED=True,
    REDDIT_OAUTH_CLIENT_ID="client-id",
    REDDIT_OAUTH_CLIENT_SECRET="client-secret",
    REDDIT_OAUTH_REDIRECT_URI="https://example.test/accounts/reddit/login/callback/",
)
def test_reddit_oauth_check_returns_no_errors_when_complete():
    """System check should succeed when Reddit OAuth config is complete."""

    assert check_reddit_oauth_settings() == []
