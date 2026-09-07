"""Regression coverage for transitional all-node ops ownership."""

from config.settings.apps import _resolve_installed_app_entries


def test_ops_remains_installed_when_role_profile_requests_disable():
    """apps.ops stays universal while core-boundary compatibility requires it."""
    installed_apps = _resolve_installed_app_entries(
        node_role="Control",
        profile_enabled=True,
        enabled_app_lock_entries=None,
        disabled_apps=("ops",),
    )

    assert "apps.ops" in installed_apps
