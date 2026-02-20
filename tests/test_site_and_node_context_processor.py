import pytest
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory

from config.context_processors import site_and_node


@pytest.mark.django_db
@pytest.mark.regression
def test_site_and_node_recovers_from_disallowed_host(monkeypatch):
    """Ensure badge context generation does not fail when host validation fails."""
    request = RequestFactory().get("/admin/", HTTP_HOST="invalid.example:8888")

    def _raise_disallowed_host():
        raise DisallowedHost

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == "invalid.example"


@pytest.mark.django_db
def test_site_and_node_disallowed_host_uses_ipv6_literal(monkeypatch):
    """Bare IPv6 fallback should preserve the literal instead of truncating it."""
    request = RequestFactory().get("/admin/", HTTP_HOST="::1")

    def _raise_disallowed_host():
        raise DisallowedHost

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == "::1"


@pytest.mark.django_db
def test_site_and_node_disallowed_host_strips_ipv6_port(monkeypatch):
    """Bare IPv6 fallback with :port should strip only the numeric port."""
    request = RequestFactory().get("/admin/", HTTP_HOST="::1:8080")

    def _raise_disallowed_host():
        raise DisallowedHost

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == "::1"


@pytest.mark.django_db
def test_site_and_node_disallowed_host_rejects_unsafe_header(monkeypatch):
    """Unsafe host characters should not be returned into the template context."""
    request = RequestFactory().get("/admin/", HTTP_HOST='bad"><script>alert(1)</script>')

    def _raise_disallowed_host():
        raise DisallowedHost

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == ""
