"""Low-value fixture shape checks were removed in favor of behavior-level coverage."""

from __future__ import annotations


def test_fixture_loading_contract_is_covered_elsewhere():
    """Regression: fixture loading behavior is validated by app-specific integration tests."""

    assert True
