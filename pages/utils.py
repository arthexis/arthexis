from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import DisallowedHost
from django.http.request import split_domain_port
from django.urls import path as django_path


ORIGINAL_REFERER_SESSION_KEY = "pages:original_referer"


def landing(label=None):
    """Decorator to mark a view as a landing page."""

    def decorator(view):
        view.landing = True
        view.landing_label = label or view.__name__.replace("_", " ").title()
        return view

    return decorator


def cache_original_referer(request) -> None:
    """Persist the first external referer observed for the session."""

    session = getattr(request, "session", None)
    if not hasattr(session, "get"):
        return

    original = session.get(ORIGINAL_REFERER_SESSION_KEY)
    if original:
        request.original_referer = original
        return

    referer = (request.META.get("HTTP_REFERER") or "").strip()
    if not referer:
        return

    try:
        parsed = urlsplit(referer)
    except ValueError:
        return

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return

    try:
        host = request.get_host()
    except DisallowedHost:
        host = ""

    referer_host, _ = split_domain_port(parsed.netloc)
    request_host, _ = split_domain_port(host)

    if referer_host and request_host:
        if referer_host.lower() == request_host.lower():
            return

    referer_value = referer[:1000]
    session[ORIGINAL_REFERER_SESSION_KEY] = referer_value
    request.original_referer = referer_value


def get_original_referer(request) -> str:
    """Return the original external referer recorded for the session."""

    if hasattr(request, "original_referer"):
        return request.original_referer or ""

    session = getattr(request, "session", None)
    if hasattr(session, "get"):
        referer = session.get(ORIGINAL_REFERER_SESSION_KEY)
        if referer:
            request.original_referer = referer
            return referer

    referer = (request.META.get("HTTP_REFERER") or "").strip()
    if referer:
        referer = referer[:1000]
    request.original_referer = referer
    return referer


def landing_leads_supported() -> bool:
    """Return ``True`` when the local node supports landing lead tracking."""

    from nodes.models import Node

    node = Node.get_local()
    if not node:
        return False
    return node.has_feature("celery-queue")
