from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory

from apps.widgets import register_widget
from apps.widgets.models import Widget, WidgetProfile, WidgetZone
from apps.widgets.services import render_zone_widgets, sync_registered_widgets
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def clear_registry(monkeypatch):
    from apps import widgets as widgets_module
    from apps.widgets import registry

    monkeypatch.setattr(registry, "_WIDGET_REGISTRY", {})
    monkeypatch.setattr(widgets_module, "register_widget", registry.register_widget)
    yield

def test_render_zone_widgets_respects_profiles():
    User = get_user_model()
    user = User.objects.create_user(username="demo")
    group = Group.objects.create(name="demo-group")
    user.groups.add(group)
    request = RequestFactory().get("/")
    request.user = user

    @register_widget(
        slug="sample",
        name="Sample",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
    )
    def _render(**kwargs):
        return {"message": "visible"}

    sync_registered_widgets()
    widget = Widget.objects.get(slug="sample")

    # Hidden without a matching profile when profiles exist.
    WidgetProfile.objects.create(widget=widget, group=group, is_enabled=False)
    assert render_zone_widgets(request=request, zone_slug=WidgetZone.ZONE_SIDEBAR) == []

    WidgetProfile.objects.all().delete()
    WidgetProfile.objects.create(widget=widget, group=group, is_enabled=True)
    rendered = render_zone_widgets(request=request, zone_slug=WidgetZone.ZONE_SIDEBAR)
    assert rendered and "visible" in rendered[0].html


def test_render_zone_widgets_respects_required_node_feature(monkeypatch):
    """Widgets tied to disabled features should be hidden until assigned."""

    User = get_user_model()
    user = User.objects.create_user(username="feature-user")
    request = RequestFactory().get("/")
    request.user = user
    node = Node.objects.create(hostname="widget-node", current_relation=Node.Relation.SELF)
    monkeypatch.setattr(node, "sync_feature_tasks", lambda: None)
    request.badge_node = node
    feature = NodeFeature.objects.create(slug="video-cam", display="Video Camera")

    @register_widget(
        slug="feature-widget",
        name="Feature Widget",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
        required_feature_slug="video-cam",
    )
    def _render_feature_widget(**_kwargs):
        return {"message": "feature-visible"}

    sync_registered_widgets()
    assert render_zone_widgets(request=request, zone_slug=WidgetZone.ZONE_SIDEBAR) == []

    NodeFeatureAssignment.objects.create(node=node, feature=feature)
    rendered = render_zone_widgets(request=request, zone_slug=WidgetZone.ZONE_SIDEBAR)
    assert rendered and "feature-visible" in rendered[0].html


def test_sync_registered_widgets_preserves_existing_required_feature_when_slug_missing():
    """Regression: widget feature binding persists when the configured slug stops resolving."""
    feature = NodeFeature.objects.create(slug="video-cam", display="Video Camera")

    @register_widget(
        slug="camera-widget",
        name="Camera Widget",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
        required_feature_slug="video-cam",
    )
    def _render_camera_widget(**_kwargs):
        return {"message": "camera"}

    sync_registered_widgets()
    widget = Widget.objects.get(slug="camera-widget")
    assert widget.required_feature == feature

    @register_widget(
        slug="camera-widget",
        name="Camera Widget",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
        required_feature_slug="video-cam-missing",
    )
    def _render_camera_widget_missing_feature(**_kwargs):
        return {"message": "camera"}

    sync_registered_widgets()
    widget.refresh_from_db()
    assert widget.required_feature_id == feature.id
