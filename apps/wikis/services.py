from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Any

import requests
from django.core.cache import cache
from django.utils.text import slugify

from .models import (
    DEFAULT_USER_AGENT,
    DEFAULT_WIKIMEDIA_ENDPOINT,
    WikiSummary,
    WikimediaBridge,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WikiRequest:
    """Request details used for Wikimedia lookups."""

    bridge: WikimediaBridge
    title: str

    @property
    def cache_key(self) -> str:
        slug = slugify(self.title) or "unknown"
        return f"wikis:summary:{self.bridge.slug}:{slug}"

    @property
    def headers(self) -> dict[str, str]:
        ua = (self.bridge.user_agent or DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT
        return {"User-Agent": ua}

    def build_params(self) -> dict[str, Any]:
        params = {
            "action": "query",
            "prop": "extracts|info",
            "format": "json",
            "redirects": 1,
            "exintro": 1,
            "titles": self.title,
            "inprop": "url",
            "formatversion": 2,
        }
        if self.bridge.language_code:
            params["uselang"] = self.bridge.language_code
        return params

    def endpoint(self) -> str:
        return (self.bridge.api_endpoint or DEFAULT_WIKIMEDIA_ENDPOINT).strip()


def _default_bridge(slug: str | None = None) -> WikimediaBridge | None:
    query = WikimediaBridge.objects.filter(is_deleted=False)
    if slug:
        match = query.filter(slug=slug).first()
        if match:
            return match
    return query.first()


def _parse_summary(response: requests.Response, language: str) -> WikiSummary | None:
    try:
        payload: dict[str, Any] = response.json()
    except Exception:
        logger.exception("Failed to parse Wikimedia response")
        return None

    try:
        pages = payload.get("query", {}).get("pages", [])
        page = pages[0] if pages else None
        if not page:
            return None
        extract = (page.get("extract") or "").strip()
        if not extract:
            return None
        title = page.get("title") or ""
        url = page.get("fullurl") or page.get("canonicalurl")
        return WikiSummary(title=title or "", extract=extract, url=url, language=language, raw=payload)
    except Exception:
        logger.exception("Could not parse Wikimedia payload")
        return None


def fetch_wiki_summary(topic: str, *, bridge_slug: str | None = None) -> WikiSummary | None:
    """Return a cached Wikipedia summary for ``topic`` if available."""

    topic = (topic or "").strip()
    if not topic:
        return None

    bridge = _default_bridge(bridge_slug)
    if bridge is None:
        return None

    request = WikiRequest(bridge=bridge, title=topic)
    cache_key = request.cache_key

    cached = cache.get(cache_key)
    if isinstance(cached, WikiSummary):
        return cached

    def _retrieve() -> WikiSummary | None:
        endpoint = request.endpoint()
        params = request.build_params()
        response = None
        try:
            response = requests.get(
                endpoint,
                params=params,
                headers=request.headers,
                timeout=bridge.timeout,
            )
            if response.status_code >= 400:
                logger.warning(
                    "Wikimedia API returned %s for %s", response.status_code, bridge.slug
                )
                return None
            summary = _parse_summary(response, bridge.language_code)
            return summary
        except Exception:
            logger.exception("Error fetching Wikimedia summary for %s", topic)
            return None
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()

    summary = _retrieve()
    if summary:
        cache.set(cache_key, summary, timeout=bridge.cache_timeout)
    return summary


def wiki_summary_or_placeholder(topic: str, *, bridge_slug: str | None = None) -> WikiSummary | None:
    return fetch_wiki_summary(topic, bridge_slug=bridge_slug)


__all__ = [
    "fetch_wiki_summary",
    "wiki_summary_or_placeholder",
    "WikiRequest",
]
