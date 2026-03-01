from django.contrib import admin
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .models import PyxelViewport


@admin.register(PyxelViewport)
class PyxelViewportAdmin(admin.ModelAdmin):
    change_form_template = "admin/pyxel/pyxelviewport/change_form.html"
    list_display = (
        "name",
        "slug",
        "is_default",
        "skin",
        "columns",
        "rows",
        "resolution_width",
        "resolution_height",
        "pyxel_fps",
    )
    search_fields = ("name", "slug", "skin", "pyxel_script")
    dashboard_actions = ["open_viewport_dashboard"]

    def get_dashboard_actions(self, request):
        """Expose dashboard-only quick actions for the model row."""

        if not self.has_view_or_change_permission(request):
            return []
        return list(self.dashboard_actions)

    def open_viewport_dashboard(self, request):
        """Describe the dashboard action that points to the viewport changelist."""

    open_viewport_dashboard.label = _("Open Viewport")
    open_viewport_dashboard.dashboard_url = "admin:pyxel_pyxelviewport_changelist"

    def changelist_view(self, request, extra_context=None):
        """Inject the Open Viewport action link into changelist object tools."""

        context = dict(extra_context or {})
        context["open_viewport_url"] = reverse("admin-pyxel-open-viewport")
        return super().changelist_view(request, extra_context=context)
