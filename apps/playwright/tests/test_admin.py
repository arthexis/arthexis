"""Admin tests for Playwright models."""

from django.contrib import admin

from apps.playwright.admin import SessionCookieAdmin
from apps.playwright.models import SessionCookie


def test_session_cookie_admin_excludes_raw_cookies_field():
    """Session cookie secrets should not be editable from the admin form."""
    admin_instance = SessionCookieAdmin(SessionCookie, admin.site)

    assert "cookies" in admin_instance.exclude
