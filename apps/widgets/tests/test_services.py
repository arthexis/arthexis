from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.widgets import register_widget
from apps.widgets.models import Widget, WidgetProfile, WidgetZone
from apps.widgets.services import evaluate_widget_visibility, render_zone_widgets, sync_registered_widgets

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def clear_registry(monkeypatch):
    from apps import widgets as widgets_module
    from apps.widgets import registry

    monkeypatch.setattr(registry, "_WIDGET_REGISTRY", {})
    monkeypatch.setattr(widgets_module, "register_widget", registry.register_widget)
    yield

def test_render_zone_widgets_syncs_when_zone_is_missing_new_registered_widget():
    """Existing zones should auto-sync when new widget registrations are introduced."""

    User = get_user_model()
    user = User.objects.create_user(username="sync-user")
    request = RequestFactory().get("/")
    request.user = user

    @register_widget(
        slug="existing-widget",
        name="Existing Widget",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
    )
    def _render_existing_widget(**_kwargs):
        return {"message": "existing"}

    sync_registered_widgets()
    assert Widget.objects.filter(slug="existing-widget").exists()

    @register_widget(
        slug="new-widget",
        name="New Widget",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
    )
    def _render_new_widget(**_kwargs):
        return {"message": "new"}

    assert not Widget.objects.filter(slug="new-widget").exists()

    rendered = render_zone_widgets(request=request, zone_slug=WidgetZone.ZONE_SIDEBAR)

    assert Widget.objects.filter(slug="new-widget").exists()
    assert {item.widget.slug for item in rendered} >= {"existing-widget", "new-widget"}

def test_evaluate_widget_visibility_returns_permission_and_profile_blockers():
    User = get_user_model()
    user = User.objects.create_user(username="visibility-user")
    request = RequestFactory().get("/")
    request.user = user

    @register_widget(
        slug="blocked-by-permission",
        name="Blocked by Permission",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
        permission=lambda **_kwargs: False,
    )
    def _render_blocked_permission(**_kwargs):
        return {"message": "nope"}

    @register_widget(
        slug="blocked-by-profile",
        name="Blocked by Profile",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
    )
    def _render_blocked_profile(**_kwargs):
        return {"message": "nope"}

    sync_registered_widgets()
    permission_widget = Widget.objects.get(slug="blocked-by-permission")
    profile_widget = Widget.objects.get(slug="blocked-by-profile")

    _, permission_blocker = evaluate_widget_visibility(widget=permission_widget, request=request)
    assert permission_blocker == "missing_permission"

    WidgetProfile.objects.create(widget=profile_widget, user=user, is_enabled=False)
    _, profile_blocker = evaluate_widget_visibility(widget=profile_widget, request=request)
    assert profile_blocker == "profile_restriction"
