from apps.core.services.profile_apps import app_selector_installed, profile_skip_reason


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
