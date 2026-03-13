import types

import pytest
from django.core.cache import cache
from django.db.utils import OperationalError
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.features.models import Feature
from apps.modules.models import Module
from apps.nodes.models import NodeFeature, NodeRole
from apps.sites.models import Landing
from apps.sites import context_processors

@pytest.mark.django_db
@pytest.mark.parametrize(
    ("is_enabled", "enable_public_chat", "expected_chat_enabled"),
    [
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_nav_links_chat_enabled_requires_feature_and_site_or_profile(
    monkeypatch, settings, is_enabled, enable_public_chat, expected_chat_enabled
):
    """Regression: chat enablement requires suite feature and a site/user opt-in signal."""

    cache.clear()
    request = RequestFactory().get("/")
    settings.PAGES_CHAT_ENABLED = True

    monkeypatch.setattr(context_processors.Node, "get_local", staticmethod(lambda: None))
    monkeypatch.setattr(
        context_processors,
        "get_site",
        lambda _request: types.SimpleNamespace(id=1, template=None, enable_public_chat=enable_public_chat),
    )

    Feature.objects.update_or_create(
        slug="staff-chat-bridge",
        defaults={"display": "Staff Chat Bridge", "is_enabled": is_enabled},
    )

    context = context_processors.nav_links(request)

    assert context["chat_enabled"] is expected_chat_enabled
