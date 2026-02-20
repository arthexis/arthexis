import pytest
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory

from config.context_processors import site_and_node


def _raise_disallowed_host():
    raise DisallowedHost


@pytest.mark.django_db
@pytest.mark.regression
def test_site_and_node_recovers_from_disallowed_host(monkeypatch):
    """Ensure badge context generation does not fail when host validation fails."""
    request = RequestFactory().get("/admin/", HTTP_HOST="invalid.example:8888")

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == "invalid.example"


@pytest.mark.django_db
@pytest.mark.regression
def test_site_and_node_disallowed_host_uses_ipv6_literal(monkeypatch):
    """Bare IPv6 fallback should preserve the literal instead of truncating it."""
    request = RequestFactory().get("/admin/", HTTP_HOST="::1")

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == "::1"


@pytest.mark.django_db
@pytest.mark.regression
def test_site_and_node_disallowed_host_strips_ipv6_port(monkeypatch):
    """Bare IPv6 fallback with :port strips a trailing numeric segment as a port."""
    request = RequestFactory().get("/admin/", HTTP_HOST="::1:8080")

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == "::1"


@pytest.mark.django_db
@pytest.mark.regression
def test_site_and_node_disallowed_host_strips_bracketed_ipv6_port(monkeypatch):
    """Canonical bracketed IPv6 host with :port should normalize to the bare IPv6."""
    request = RequestFactory().get("/admin/", HTTP_HOST="[::1]:8080")

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == "::1"


@pytest.mark.django_db
@pytest.mark.regression
def test_site_and_node_disallowed_host_rejects_unsafe_header(monkeypatch):
    """Unsafe host characters should not be returned into the template context."""
    request = RequestFactory().get("/admin/", HTTP_HOST='bad"><script>alert(1)</script>')

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == ""


@pytest.mark.django_db
@pytest.mark.regression
def test_site_and_node_disallowed_host_falls_back_to_server_name(monkeypatch):
    """SERVER_NAME is used when HTTP_HOST is absent and get_host raises."""
    request = RequestFactory().get("/admin/")
    request.META.pop("HTTP_HOST", None)
    request.META["SERVER_NAME"] = "server.example"

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == "server.example"
