from django.conf import settings
from django.urls import resolve, reverse


def test_project_uses_monterrey_timezone():
    assert settings.TIME_ZONE == "America/Monterrey"
    assert settings.USE_TZ is True


def test_dashboard_app_is_installed():
    assert "dashboard" in settings.INSTALLED_APPS


def test_root_url_resolves_to_dashboard_index():
    assert resolve(reverse("dashboard:index")).view_name == "dashboard:index"
