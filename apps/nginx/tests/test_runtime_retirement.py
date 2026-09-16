import pytest
from django.apps import apps
from django.conf import settings

from config.settings.nginx_retirement import apply_nginx_runtime_retirement


def test_site_configuration_is_not_registered_at_runtime():
    """The retired nginx app exposes migration history, not live models."""

    assert "apps.nginx" not in settings.INSTALLED_APPS
    with pytest.raises(LookupError):
        apps.get_model("nginx", "SiteConfiguration")


def test_nginx_shell_is_loaded_for_migrations():
    installed_apps = ["apps.core"]

    apply_nginx_runtime_retirement(installed_apps, argv=("migrate",))

    assert installed_apps == ["apps.core", "apps.certs", "apps.nginx"]


def test_nginx_shell_is_not_loaded_for_runtime():
    installed_apps = ["apps.nginx", "apps.core"]

    apply_nginx_runtime_retirement(installed_apps, argv=("runserver",))

    assert installed_apps == ["apps.core"]
