"""Regression tests for Reddit OAuth social-auth configuration checks."""

import pytest
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
)
def test_reddit_oauth_validation_raises_specific_exception_for_missing_values():
    """Missing Reddit OAuth settings should raise RedditOAuthConfigurationError."""

    with pytest.raises(RedditOAuthConfigurationError) as excinfo:
        validate_reddit_oauth_settings()
    message = str(excinfo.value)

    assert "REDDIT_OAUTH_CLIENT_ID" in message
    assert "REDDIT_OAUTH_CLIENT_SECRET" in message


@override_settings(
    REDDIT_AUTH_ENABLED=True,
    REDDIT_OAUTH_CLIENT_ID="client-id",
    REDDIT_OAUTH_CLIENT_SECRET="client-secret",
)
def test_reddit_oauth_check_returns_no_errors_when_complete():
    """System check should succeed when Reddit OAuth config is complete."""

    assert check_reddit_oauth_settings() == []
