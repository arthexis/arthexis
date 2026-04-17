from contextvars import ContextVar

from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin
from apps.widgets.services import evaluate_widget_visibility

from .models import Widget, WidgetProfile, WidgetZone

_changelist_request: ContextVar = ContextVar("widgets_changelist_request", default=None)


@admin.register(WidgetZone)
class WidgetZoneAdmin(EntityModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")


@admin.register(Widget)
class WidgetAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "slug",
        "zone",
        "required_feature",
        "is_enabled",
        "visibility_for_current_user",
        "priority",
    )
    list_filter = ("zone", "is_enabled")
    search_fields = ("name", "slug", "renderer_path")
    ordering = ("priority", "name")

    @admin.display(description="Sidebar visibility")
    def visibility_for_current_user(self, obj: Widget) -> str:
        if obj.zone.slug != WidgetZone.ZONE_SIDEBAR:
            return "N/A (non-sidebar zone)"
        if not obj.is_enabled:
            return "Hidden: widget disabled"
        request = _changelist_request.get()
        if request is None:
            return "Unknown"
        _, blocker = evaluate_widget_visibility(widget=obj, request=request)
        if blocker == "missing_permission":
            return "Hidden: missing permission"
        if blocker == "missing_required_feature":
            return "Hidden: missing required feature"
        if blocker == "profile_restriction":
            return "Hidden: profile restriction"
        if blocker == "missing_registration":
            return "Hidden: widget not registered"
        return "Visible"

    def changelist_view(self, request, extra_context=None):
        token = _changelist_request.set(request)
        try:
            return super().changelist_view(request, extra_context=extra_context)
        finally:
            _changelist_request.reset(token)


@admin.register(WidgetProfile)
class WidgetProfileAdmin(EntityModelAdmin):
    list_display = ("widget", "user", "group", "is_enabled")
    list_filter = ("is_enabled", "group")
    search_fields = ("widget__name", "widget__slug", "user__username", "group__name")
