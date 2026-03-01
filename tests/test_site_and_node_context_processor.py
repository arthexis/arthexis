import pytest
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory

from apps.sites.models import AdminBadge
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
    """Bare IPv6 fallback with :port strips a trailing numeric segment as a port.

    NOTE: ``::1:8080`` is ambiguous under RFC 2732; canonical IPv6+port uses
    ``[::1]:8080``. This pins the _resolve_request_host best-effort heuristic.
    """
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


@pytest.mark.django_db
def test_admin_badges_ignore_unauthorized_callable_path(django_user_model):
    user = django_user_model.objects.create_user(username="staff", is_staff=True)
    request = RequestFactory().get("/admin/")
    request.user = user

    AdminBadge.objects.create(
        slug="bad",
        name="Bad",
        label="BAD",
        value_query_path="os.system",
        is_enabled=True,
    )

    context = site_and_node(request)

    assert context["admin_badges"][0]["value"] == "Unknown"
    assert context["admin_badges"][0]["is_present"] is False


@pytest.mark.django_db
def test_admin_badges_handle_non_dict_payload(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="staff-2", is_staff=True)
    request = RequestFactory().get("/admin/")
    request.user = user

    AdminBadge.objects.create(
        slug="site",
        name="Site",
        label="SITE",
        value_query_path="apps.sites.admin_badges.site_badge_data",
        is_enabled=True,
    )

    monkeypatch.setattr(
        "apps.sites.admin_badges.site_badge_data",
        lambda **_kwargs: "unexpected",
    )

    context = site_and_node(request)

    assert context["admin_badges"][0]["value"] == "Unknown"
    assert context["admin_badges"][0]["is_present"] is False
