"""Redirect helpers for ops views and admin actions."""

from django.http import HttpRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def safe_host_redirect(request: HttpRequest, url: str, *, fallback: str = "admin:index"):
    """Redirect only to same-host/scheme URLs, otherwise fall back to a named route."""

    target = url or ""
    if not url_has_allowed_host_and_scheme(
        url=target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        target = reverse(fallback)
    return redirect(target)
