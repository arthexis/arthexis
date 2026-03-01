import types

import pytest
from django.core.cache import cache
from django.db.utils import OperationalError
from django.test import RequestFactory

from apps.features.models import Feature
from apps.modules.models import Module
from apps.nodes.models import NodeFeature
from apps.sites.models import Landing
from apps.sites import context_processors


@pytest.mark.django_db
@pytest.mark.integration
def test_nav_links_handles_missing_modules_table(monkeypatch):
    request = RequestFactory().get("/admin/")

    monkeypatch.setattr(context_processors.Node, "get_local", staticmethod(lambda: None))

    class BrokenQuerySet:
        def filter(self, **kwargs):
            return self

        def select_related(self, *args, **kwargs):
            return self

        def prefetch_related(self, *args, **kwargs):
            return self

        def __iter__(self):
            raise OperationalError("modules table missing")

    monkeypatch.setattr(
        context_processors.Module.objects,
        "for_role",
        types.MethodType(lambda self, role: BrokenQuerySet(), context_processors.Module.objects),
    )

    context = context_processors.nav_links(request)

    assert context["nav_modules"] == []


@pytest.mark.django_db
@pytest.mark.integration
def test_nav_links_hides_modules_with_disabled_features(monkeypatch):
    request = RequestFactory().get("/apps/")

    monkeypatch.setattr(context_processors.Node, "get_local", staticmethod(lambda: None))
    monkeypatch.setattr(NodeFeature, "is_enabled", property(lambda self: False))

    feature = NodeFeature.objects.create(slug="rfid-scanner", display="RFID Scanner")
    module = Module.objects.create(path="apps")
    module.features.add(feature)
    Landing.objects.create(module=module, path="/apps/", label="Apps")

    context = context_processors.nav_links(request)

    assert context["nav_modules"] == []


@pytest.mark.django_db
def test_nav_links_hides_landings_with_disabled_required_features(monkeypatch):
    request = RequestFactory().get("/ocpp/")

    monkeypatch.setattr(context_processors.Node, "get_local", staticmethod(lambda: None))
    monkeypatch.setattr(NodeFeature, "is_enabled", property(lambda self: False))

    NodeFeature.objects.create(slug="rfid-scanner", display="RFID Scanner")
    NodeFeature.objects.create(slug="video-cam", display="Video Camera")
    module = Module.objects.create(path="ocpp")
    Landing.objects.create(
        module=module,
        path="/ocpp/rfid/validator/",
        label="RFID Card Validator",
    )

    context = context_processors.nav_links(request)

    assert context["nav_modules"] == []


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


@pytest.mark.django_db
def test_nav_links_chat_disabled_when_staff_chat_bridge_missing(monkeypatch, settings):
    """Chat should be disabled when staff-chat-bridge suite feature is absent."""

    cache.clear()
    request = RequestFactory().get("/")
    settings.PAGES_CHAT_ENABLED = True

    monkeypatch.setattr(context_processors.Node, "get_local", staticmethod(lambda: None))
    monkeypatch.setattr(
        context_processors,
        "get_site",
        lambda _request: types.SimpleNamespace(id=1, template=None, enable_public_chat=True),
    )

    Feature.objects.filter(slug="staff-chat-bridge").delete()

    context = context_processors.nav_links(request)

    assert context["chat_enabled"] is False
