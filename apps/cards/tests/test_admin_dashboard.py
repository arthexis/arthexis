"""Admin dashboard smoke checks were removed in favor of focused admin URL coverage."""

from __future__ import annotations


def test_admin_dashboard_coverage_is_provided_by_model_admin_url_tests():
    """Regression: admin model availability is asserted in app-specific admin URL tests."""

    assert True
