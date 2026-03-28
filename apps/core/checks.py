"""System checks for repository-wide runtime invariants."""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, register


@register()
def no_legacy_migration_apps_registered(app_configs, **kwargs):
    """Ensure 1.0+ deployments do not load legacy migration-only app shims."""

    del app_configs, kwargs

    legacy_apps = sorted(
        app_path
        for app_path in settings.INSTALLED_APPS
        if app_path.startswith("apps._legacy.")
    )
    if not legacy_apps:
        return []

    return [
        Error(
            "Legacy migration-only apps remain in INSTALLED_APPS.",
            hint=(
                "Remove legacy app entries from manifest wiring and settings so "
                "1.0+ deployments run without pre-1.0 migration shims."
            ),
            obj=", ".join(legacy_apps),
            id="core.E100",
        )
    ]
