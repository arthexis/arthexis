"""Tests for share-link context processor helpers."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from apps.links.context_processors import share_short_url

pytestmark = [pytest.mark.django_db, pytest.mark.pr_origin(9999)]


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
