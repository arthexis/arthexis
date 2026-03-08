from __future__ import annotations

from typing import Iterable


def normalize_playwright_cookie(cookie: dict) -> dict:
    """Return a Playwright-compatible cookie payload with an inferred ``url``."""

    payload = dict(cookie)
    if "url" not in payload:
        domain = payload.get("domain", "localhost")
        scheme = "https" if payload.get("secure") else "http"
        payload["url"] = f"{scheme}://{domain.lstrip('.')}"
    return payload


def normalize_playwright_cookies(cookies: Iterable[dict]) -> list[dict]:
    """Normalize an iterable of cookie mappings for ``BrowserContext.add_cookies``."""

    return [normalize_playwright_cookie(cookie) for cookie in cookies]
