"""Runtime controls for automatic GitHub issue reporting."""

from __future__ import annotations

from django.conf import settings

GITHUB_ISSUE_REPORTING_FEATURE_SLUG = "github-issue-reporting"


def is_github_issue_reporting_enabled() -> bool:
    """Return whether automatic GitHub exception reporting should run.

    Returns:
        ``True`` when the suite feature is enabled, falling back to the legacy
        Django setting while feature rows are unavailable or not yet seeded.
    """

    from apps.features.utils import is_suite_feature_enabled

    default_enabled = getattr(settings, "GITHUB_ISSUE_REPORTING_ENABLED", True)
    return is_suite_feature_enabled(
        GITHUB_ISSUE_REPORTING_FEATURE_SLUG,
        default=default_enabled,
    )
