from __future__ import annotations

from typing import Dict


def add_headers(headers: Dict[str, str], path: str, url: str) -> None:
    """Allow cross-origin access to bundled front-end dependencies.

    This ensures locally served fallbacks (e.g., htmx assets) can be loaded from
    other origins when CDN access is blocked.
    """

    if url.startswith("/static/htmx/"):
        headers["Access-Control-Allow-Origin"] = "*"
