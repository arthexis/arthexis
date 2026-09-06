from apps.app.services.profile_apps import app_selector_installed, profile_skip_reason
from apps.core.services.profile_apps import (
    app_selector_installed as legacy_app_selector_installed,
)
from apps.core.services.profile_apps import profile_skip_reason as legacy_profile_skip_reason


def test_profile_app_matching_does_not_confuse_django_sites_with_local_sites():
    installed_apps = ["django.contrib.sites", "apps.core"]

    assert not app_selector_installed("apps.sites", installed_apps=installed_apps)
    assert (
        profile_skip_reason(
            app_selector="apps.sites",
            installed_apps=installed_apps,
        )
        == "apps.sites is not installed for this node profile"
    )


def test_core_profile_app_imports_are_compatibility_aliases():
    assert legacy_app_selector_installed is app_selector_installed
    assert legacy_profile_skip_reason is profile_skip_reason
