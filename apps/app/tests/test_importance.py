"""Tests for application importance choices."""

from apps.app.models import Application


def test_application_importance_includes_legacy_choice() -> None:
    assert Application.Importance.LEGACY == "legacy"
    assert ("legacy", "Legacy") in Application.Importance.choices
