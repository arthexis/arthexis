"""Regression tests for project-level URL helper exports."""

from config import urls


def test_legacy_autodiscovered_urlpatterns_alias_matches_route_provider():
    """config.urls should keep the legacy helper alias for alternate URLConfs.

    Parameters:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the legacy alias no longer points at the route provider.
    """

    assert urls.autodiscovered_urlpatterns is urls.autodiscovered_route_patterns
