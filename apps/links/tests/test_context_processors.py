"""Tests for share-link context processor helpers."""

from __future__ import annotations

import pytest
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory

from apps.links.context_processors import share_short_url

pytestmark = [pytest.mark.django_db, pytest.mark.pr_origin(6230)]


def test_share_short_url_returns_qr_data_uri(monkeypatch):
    """Share context should include both the short URL and its QR image."""

    class _ShortURL:
        def redirect_path(self) -> str:
            return "/links/s/abc123/"

    monkeypatch.setattr(
        "apps.links.context_processors.get_or_create_short_url",
        lambda _target: _ShortURL(),
    )

    request = RequestFactory().get("/", HTTP_HOST="example.com")

    context = share_short_url(request)

    assert context["share_short_url"] == "http://example.com/links/s/abc123/"
    assert context["share_short_url_qr"].startswith("data:image/png;base64,")


def test_share_short_url_falls_back_to_page_url_when_short_url_unavailable(monkeypatch):
    """Share context should still provide QR metadata without a short URL record."""

    monkeypatch.setattr(
        "apps.links.context_processors.get_or_create_short_url",
        lambda _target: None,
    )

    request = RequestFactory().get("/docs/", HTTP_HOST="example.com")

    context = share_short_url(request)

    assert context["share_short_url"] == "http://example.com/docs/"
    assert context["share_short_url_qr"].startswith("data:image/png;base64,")


@pytest.mark.pr_origin(6236)
def test_share_short_url_falls_back_to_relative_path_on_disallowed_host(monkeypatch):
    """Disallowed hosts should produce safe relative share URLs."""

    monkeypatch.setattr(
        "apps.links.context_processors.get_or_create_short_url",
        lambda _target: None,
    )

    request = RequestFactory().get("/docs/", HTTP_HOST="attacker.invalid")

    context = share_short_url(request)

    assert context["share_short_url"] == "/docs/"
    assert context["share_short_url_qr"].startswith("data:image/png;base64,")




@pytest.mark.pr_origin(6250)
def test_share_short_url_rebuilds_absolute_url_for_trusted_disallowed_host(monkeypatch):
    """Trusted hosts should still generate absolute URLs when Django rejects request host."""

    class _Site:
        """Minimal site object used to provide a trusted domain in tests."""

        domain = "example.com"

    monkeypatch.setattr(
        "apps.links.context_processors.get_or_create_short_url",
        lambda _target: None,
    )
    monkeypatch.setattr(
        "apps.links.context_processors.Site.objects.get_current",
        lambda: _Site(),
    )

    request = RequestFactory().get("/docs/", HTTP_HOST="example.com")
    monkeypatch.setattr(
        request,
        "build_absolute_uri",
        lambda _path: (_ for _ in ()).throw(DisallowedHost("bad host")),
    )

    context = share_short_url(request)

    assert context["share_short_url"] == "http://example.com/docs/"


@pytest.mark.pr_origin(6250)
def test_share_short_url_rejects_malformed_trusted_fallback_host(monkeypatch):
    """Fallback should reject authorities whose effective host differs from site domain."""

    class _Site:
        """Minimal site object used to provide a trusted domain in tests."""

        domain = "example.com"

    monkeypatch.setattr(
        "apps.links.context_processors.get_or_create_short_url",
        lambda _target: None,
    )
    monkeypatch.setattr(
        "apps.links.context_processors.Site.objects.get_current",
        lambda: _Site(),
    )

    request = RequestFactory().get("/docs/", HTTP_HOST="example.com:80@evil.com")
    monkeypatch.setattr(
        request,
        "build_absolute_uri",
        lambda _path: (_ for _ in ()).throw(DisallowedHost("bad host")),
    )

    context = share_short_url(request)

    assert context["share_short_url"] == "/docs/"


@pytest.mark.pr_origin(6250)
def test_share_short_url_rejects_port_mismatch_with_trusted_site(monkeypatch):
    """Fallback should reject trusted hosts when configured site port does not match."""

    class _Site:
        """Minimal site object used to provide a trusted domain in tests."""

        domain = "example.com:8080"

    monkeypatch.setattr(
        "apps.links.context_processors.get_or_create_short_url",
        lambda _target: None,
    )
    monkeypatch.setattr(
        "apps.links.context_processors.Site.objects.get_current",
        lambda: _Site(),
    )

    request = RequestFactory().get("/docs/", HTTP_HOST="example.com:9000")
    monkeypatch.setattr(
        request,
        "build_absolute_uri",
        lambda _path: (_ for _ in ()).throw(DisallowedHost("bad host")),
    )

    context = share_short_url(request)

    assert context["share_short_url"] == "/docs/"


@pytest.mark.pr_origin(6250)
def test_share_short_url_rejects_trusted_host_with_invalid_port(monkeypatch):
    """Fallback should return the path when trusted hosts include malformed ports."""

    class _Site:
        """Minimal site object used to provide a trusted domain in tests."""

        domain = "example.com"

    monkeypatch.setattr(
        "apps.links.context_processors.get_or_create_short_url",
        lambda _target: None,
    )
    monkeypatch.setattr(
        "apps.links.context_processors.Site.objects.get_current",
        lambda: _Site(),
    )

    request = RequestFactory().get("/docs/", HTTP_HOST="example.com:bad")
    monkeypatch.setattr(
        request,
        "build_absolute_uri",
        lambda _path: (_ for _ in ()).throw(DisallowedHost("bad host")),
    )

    context = share_short_url(request)

    assert context["share_short_url"] == "/docs/"

def test_share_short_url_returns_empty_qr_when_encoding_fails(monkeypatch):
    """Share context should gracefully handle QR encoder errors."""

    monkeypatch.setattr(
        "apps.links.context_processors.get_or_create_short_url",
        lambda _target: None,
    )
    monkeypatch.setattr(
        "apps.links.context_processors._encode_share_qr_data_uri",
        lambda _target: (_ for _ in ()).throw(ValueError("bad qr")),
    )

    request = RequestFactory().get("/docs/", HTTP_HOST="example.com")

    context = share_short_url(request)

    assert context["share_short_url"] == "http://example.com/docs/"
    assert context["share_short_url_qr"] == ""
