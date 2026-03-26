"""Regression tests for site/node context generation and admin badges."""

from types import SimpleNamespace

import pytest
from django.contrib.sites.models import Site
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory

from apps.sites.models import AdminBadge
from config.context_processors import site_and_node

pytestmark = [pytest.mark.django_db]


def _raise_disallowed_host():
    """Raise DisallowedHost for host validation failure simulations."""
    raise DisallowedHost


def test_site_and_node_disallowed_host_uses_empty_domain(monkeypatch):
    """Ensure badge context generation drops invalid hosts when validation fails."""
    request = RequestFactory().get("/admin/", HTTP_HOST="invalid.example:8888")

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == ""


def test_site_and_node_disallowed_host_rejects_unsafe_header(monkeypatch):
    """Unsafe host characters should not be returned into the template context."""
    request = RequestFactory().get(
        "/admin/", HTTP_HOST='bad"><script>alert(1)</script>'
    )

    monkeypatch.setattr(request, "get_host", _raise_disallowed_host)

    context = site_and_node(request)

    assert context["current_site_domain"] == ""


def test_admin_badges_unknown_provider_key_fails_closed(django_user_model):
    """Badge resolution should fail closed for unknown providers."""
    user = django_user_model.objects.create_user(username="staff", is_staff=True)
    request = RequestFactory().get("/admin/")
    request.user = user

    badge = AdminBadge.objects.create(
        slug="bad",
        name="Bad",
        label="BAD",
        provider_key=AdminBadge.PROVIDER_SITE,
        is_enabled=True,
    )
    AdminBadge.objects.filter(pk=badge.pk).update(provider_key="unknown-provider")

    context = site_and_node(request)

    assert context["admin_badges"][0]["value"] == "Unknown"
    assert context["admin_badges"][0]["is_present"] is False


@pytest.mark.parametrize(
    ("provider_key", "request_attrs", "slug", "expected_value", "expected_pk"),
    [
        (
            AdminBadge.PROVIDER_SITE,
            {"badge_site": Site(id=101, name="HQ", domain="hq.example")},
            "site",
            "HQ",
            101,
        ),
        (
            AdminBadge.PROVIDER_NODE,
            {"badge_node": SimpleNamespace(pk=202, hostname="node-1")},
            "node",
            "node-1",
            202,
        ),
        (
            AdminBadge.PROVIDER_ROLE,
            {
                "badge_node": SimpleNamespace(
                    pk=202,
                    hostname="node-1",
                    role=SimpleNamespace(pk=303, name="Primary"),
                )
            },
            "role",
            "Primary",
            303,
        ),
    ],
)
def test_admin_badges_valid_provider_renders_expected_payload(
    django_user_model, provider_key, request_attrs, slug, expected_value, expected_pk
):
    """Known providers should render deterministic payloads."""
    user = django_user_model.objects.create_user(
        username=f"staff-provider-{slug}",
        is_staff=True,
    )
    request = RequestFactory().get("/admin/")
    request.user = user
    for attr_name, attr_value in request_attrs.items():
        setattr(request, attr_name, attr_value)

    AdminBadge.objects.create(
        slug=f"{slug}-provider",
        name=f"{slug.title()} provider",
        label=slug.upper(),
        provider_key=provider_key,
        is_enabled=True,
    )

    context = site_and_node(request)

    assert context["admin_badges"][0]["is_present"] is True
    assert context["admin_badges"][0]["value"] == expected_value
    assert str(expected_pk) in context["admin_badges"][0]["value_url"]
