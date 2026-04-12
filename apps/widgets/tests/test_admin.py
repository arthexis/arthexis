import pytest
from django.contrib import admin
from django.template import engines
from django.template.response import TemplateResponse
from django.test import RequestFactory

from apps.locals.user_data import EntityModelAdmin
from apps.widgets.admin import WidgetAdmin
from apps.widgets.models import Widget, WidgetZone

pytestmark = pytest.mark.django_db


def test_widget_admin_changelist_keeps_request_until_template_render(monkeypatch):
    zone = WidgetZone.objects.create(name="Sidebar", slug=WidgetZone.ZONE_SIDEBAR)
    widget = Widget.objects.create(
        name="Unregistered Widget",
        slug="unregistered-widget",
        zone=zone,
        template_name="widgets/tests/sample.html",
        renderer_path="apps.widgets.tests:test",
    )

    captured = {}

    def fake_changelist_view(self, request, extra_context=None):
        template = engines["django"].from_string("ok")
        response = TemplateResponse(request=request, template=template, context={})

        def _capture_visibility(_response):
            captured["visibility"] = self.visibility_for_current_user(widget)
            return _response

        response.add_post_render_callback(_capture_visibility)
        return response

    monkeypatch.setattr(EntityModelAdmin, "changelist_view", fake_changelist_view)

    request = RequestFactory().get("/admin/widgets/widget/")
    request.user = None
    admin_instance = WidgetAdmin(Widget, admin.site)

    response = admin_instance.changelist_view(request)
    assert response.is_rendered is False

    response.render()
    assert captured["visibility"] == "Hidden: widget not registered"
