from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory

from apps.widgets import register_widget
from apps.widgets.models import Widget, WidgetProfile, WidgetZone
from apps.widgets.registry import iter_registered_widgets
from apps.widgets import services
from apps.widgets.services import render_zone_widgets, sync_registered_widgets

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_registry(monkeypatch):
    from apps import widgets as widgets_module
    from apps.widgets import registry

    monkeypatch.setattr(registry, "_WIDGET_REGISTRY", {})
    monkeypatch.setattr(widgets_module, "register_widget", registry.register_widget)
    yield


def test_register_widget_tracks_definition():
    @register_widget(
        slug="sample",
        name="Sample",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
        description="Demo",
        order=5,
    )
    def _render(**kwargs):
        return {"message": "hello"}

    definitions = iter_registered_widgets()
    assert len(definitions) == 1
    definition = definitions[0]
    assert definition.slug == "sample"
    assert definition.description == "Demo"
    assert definition.order == 5


def test_sync_registered_widgets_creates_records(settings):
    @register_widget(
        slug="sample",
        name="Sample",
        zone=WidgetZone.ZONE_APPLICATION,
        template_name="widgets/tests/sample.html",
    )
    def _render(**kwargs):
        return {"message": "hello"}

    sync_registered_widgets()

    zone = WidgetZone.objects.get(slug=WidgetZone.ZONE_APPLICATION)
    widget = Widget.objects.get(slug="sample")
    assert zone.name == "Application"
    assert widget.zone == zone
    assert widget.renderer_path.endswith("_render")
    assert widget.is_seed_data


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


def test_render_zone_widgets_syncs_when_zone_empty():
    request = RequestFactory().get("/")

    @register_widget(
        slug="sample",
        name="Sample",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
    )
    def _render(**kwargs):
        return {"message": "seeded"}

    assert not Widget.objects.filter(zone__slug=WidgetZone.ZONE_SIDEBAR).exists()

    rendered = render_zone_widgets(request=request, zone_slug=WidgetZone.ZONE_SIDEBAR)

    assert rendered and "seeded" in rendered[0].html
    assert Widget.objects.filter(zone__slug=WidgetZone.ZONE_SIDEBAR).exists()


def test_render_zone_widgets_skips_sync_when_zone_has_widgets(monkeypatch):
    request = RequestFactory().get("/")

    @register_widget(
        slug="sample",
        name="Sample",
        zone=WidgetZone.ZONE_SIDEBAR,
        template_name="widgets/tests/sample.html",
    )
    def _render(**kwargs):
        return {"message": "ignored"}

    sync_registered_widgets()
    Widget.objects.update(is_enabled=False)
    called = {"value": False}

    def _sync():
        called["value"] = True

    monkeypatch.setattr(services, "sync_registered_widgets", _sync)

    assert render_zone_widgets(request=request, zone_slug=WidgetZone.ZONE_SIDEBAR) == []
    assert called["value"] is False
