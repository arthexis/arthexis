from django.contrib import admin
from django.urls import reverse

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

    def changelist_view(self, request, extra_context=None):
        """Inject the Open Viewport action link into changelist object tools."""

        context = dict(extra_context or {})
        context["open_viewport_url"] = reverse("admin-pyxel-open-viewport")
        return super().changelist_view(request, extra_context=context)
