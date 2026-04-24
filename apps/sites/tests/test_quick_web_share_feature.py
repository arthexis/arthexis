"""Regression tests for quick web share suite feature gating."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from apps.features.models import Feature
from apps.features.utils import QUICK_WEB_SHARE_FEATURE_SLUG
from apps.links.context_processors import share_short_url
from apps.links.models import ShortURL
from apps.sites.middleware import SharePreviewPublicMiddleware

pytestmark = pytest.mark.django_db

def _set_quick_web_share_enabled(enabled: bool) -> None:
    Feature.objects.update_or_create(
        slug=QUICK_WEB_SHARE_FEATURE_SLUG,
        defaults={"display": "Quick Web Share", "is_enabled": enabled},
    )
    cache.delete("features:quick-web-share:enabled")

def test_share_context_returns_empty_values_when_feature_disabled_by_default(
    rf: RequestFactory,
) -> None:
    request = rf.get("/public/")

    context = share_short_url(request)

    assert context == {
        "quick_web_share_enabled": False,
        "share_short_url": "",
        "share_short_url_qr": "",
    }
    assert ShortURL.objects.count() == 0

def test_share_context_builds_short_url_when_feature_enabled(
    rf: RequestFactory,
) -> None:
    _set_quick_web_share_enabled(True)
    request = rf.get("/public/")

    context = share_short_url(request)

    assert context["quick_web_share_enabled"] is True
    assert "/links/s/" in context["share_short_url"]
    assert context["share_short_url_qr"].startswith("data:image/png;base64,")
    assert ShortURL.objects.count() == 1

def test_share_preview_public_middleware_requires_quick_web_share_feature(
    rf: RequestFactory,
) -> None:
    _set_quick_web_share_enabled(False)
    user = get_user_model().objects.create_user(
        username="quick-share-middleware-user",
        email="quick-share-middleware-user@example.com",
        password="secret",
    )
    request = rf.get("/?djdt=share-preview&share_preview_public=1")
    request.user = user
    middleware = SharePreviewPublicMiddleware(
        lambda req: HttpResponse(str(req.user.is_anonymous))
    )

    disabled_response = middleware(request)
    assert disabled_response.content == b"False"

    _set_quick_web_share_enabled(True)
    enabled_request = rf.get("/?djdt=share-preview&share_preview_public=1")
    enabled_request.user = user

    enabled_response = middleware(enabled_request)
    assert enabled_response.content == b"True"

