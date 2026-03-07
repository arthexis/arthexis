"""Deprecated admin entrypoint kept for import compatibility."""

from apps.playwright.admin import (  # noqa: F401
    PlaywrightBrowserAdmin as SeleniumBrowserAdmin,
    PlaywrightScriptAdmin as SeleniumScriptAdmin,
    SessionCookieAdmin,
)

__all__ = ["SeleniumBrowserAdmin", "SeleniumScriptAdmin", "SessionCookieAdmin"]
