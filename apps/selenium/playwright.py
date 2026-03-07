"""Backwards-compatible playwright helpers for the legacy selenium namespace."""

from apps.playwright.playwright import (  # noqa: F401
    normalize_playwright_cookie,
    normalize_playwright_cookies,
)

__all__ = ["normalize_playwright_cookie", "normalize_playwright_cookies"]
