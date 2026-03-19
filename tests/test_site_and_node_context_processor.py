"""Regression tests for site/node context generation and admin badges."""

import pytest
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory

from apps.sites.models import AdminBadge
from config.context_processors import site_and_node

pytestmark = pytest.mark.django_db


def _raise_disallowed_host():
    """Raise DisallowedHost for host validation failure simulations."""
    raise DisallowedHost


def test_site_and_node_recovers_from_disallowed_host(monkeypatch):
    """Ensure badge context generation does not fail when host validation fails."""
    request = RequestFactory().get("/admin/", HTTP_HOST="invalid.example:8888")

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == "invalid.example"


def test_site_and_node_disallowed_host_rejects_unsafe_header(monkeypatch):
    """Unsafe host characters should not be returned into the template context."""
    request = RequestFactory().get("/admin/", HTTP_HOST='bad"><script>alert(1)</script>')

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == ""


def test_admin_badges_ignore_unauthorized_callable_path(django_user_model):
    """Badge resolution should reject non-allowlisted callable paths."""
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


def test_admin_badges_handle_non_dict_payload(monkeypatch, django_user_model):
    """Regression: non-dict badge payloads degrade gracefully to Unknown."""
    user = django_user_model.objects.create_user(username="staff-2", is_staff=True)
    request = RequestFactory().get("/admin/")
    request.user = user

    AdminBadge.objects.create(
        slug="site-non-dict",
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
