"""Regression tests for site/node context generation host handling."""

import pytest
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory

from config.context_processors import site_and_node


pytestmark = [pytest.mark.django_db, pytest.mark.regression]


def _raise_disallowed_host():
    raise DisallowedHost


def test_site_and_node_disallowed_host_uses_empty_domain(monkeypatch):
    """Host validation failures should not leak values into template context."""

    request = RequestFactory().get("/admin/", HTTP_HOST="invalid.example:8888")
    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == ""


def test_site_and_node_disallowed_host_rejects_unsafe_header(monkeypatch):
    """Unsafe host characters should not be returned into template context."""

    request = RequestFactory().get(
        "/admin/", HTTP_HOST='bad\"><script>alert(1)</script>'
    )
    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == ""
