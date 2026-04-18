"""Tests for enabled application lock synchronization."""

from __future__ import annotations

from apps.app.models import _load_manifest_app_entries


def test_load_manifest_app_entries_includes_runtime_apps_only():
    """Manifest discovery should include runtime apps and exclude legacy shims."""

    manifest_app_entries = _load_manifest_app_entries()
    expected_apps = {
        "apps.classification",
        "apps.projects",
        "apps.special",
    }

    assert expected_apps.issubset(manifest_app_entries)
    assert all(
        not app_entry.startswith("apps._legacy.")
        for app_entry in manifest_app_entries
    )
