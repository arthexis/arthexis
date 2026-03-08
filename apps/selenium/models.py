"""Backwards-compatible aliases for the legacy selenium app namespace."""

from apps.playwright.models import (  # noqa: F401
    InvalidCookiePayloadError,
    PlaywrightBrowser,
    PlaywrightDriver,
    PlaywrightScript,
    SessionCookie,
    UnsupportedBrowserEngineError,
)

SeleniumBrowser = PlaywrightBrowser
SeleniumScript = PlaywrightScript

__all__ = [
    "InvalidCookiePayloadError",
    "PlaywrightBrowser",
    "PlaywrightDriver",
    "PlaywrightScript",
    "SessionCookie",
    "SeleniumBrowser",
    "SeleniumScript",
    "UnsupportedBrowserEngineError",
]
