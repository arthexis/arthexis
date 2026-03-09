"""Admin tests for Playwright models."""

from django.contrib import admin
from django.test import RequestFactory

from apps.playwright.admin import SessionCookieAdmin
from apps.playwright.models import SessionCookie


def test_session_cookie_admin_excludes_raw_cookies_field():
    """Session cookie secrets should not be editable from the admin form."""
    admin_instance = SessionCookieAdmin(SessionCookie, admin.site)
    request = RequestFactory().get("/")
    form = admin_instance.get_form(request, fields=("name", "cookies"))

    assert form.base_fields
    assert "cookies" not in form.base_fields
