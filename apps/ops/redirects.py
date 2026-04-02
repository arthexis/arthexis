"""Redirect helpers for ops views and admin actions."""

from urllib.parse import urlsplit, urlunsplit

from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.http import escape_leading_slashes


def safe_host_redirect(
    request: HttpRequest, url: str, *, fallback: str = "admin:index"
):
    """Redirect only to same-host/scheme URLs, otherwise fall back to a named route."""

    target = url or ""
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        target = reverse(fallback)
    else:
        path = escape_leading_slashes(parts.path)
        if not path.startswith("/"):
            target = reverse(fallback)
        else:
            target = urlunsplit(("", "", path, parts.query, parts.fragment))
    return HttpResponseRedirect(target)
