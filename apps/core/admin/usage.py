from django.contrib import admin, messages

from apps.core.analytics import usage_analytics_collection_paused
from apps.core.models import UsageEvent


@admin.register(UsageEvent)
class UsageEventAdmin(admin.ModelAdmin):
    """Read-only admin for stored usage analytics events."""

    list_display = (
        "timestamp",
        "app_label",
        "view_name",
        "method",
        "status_code",
        "action",
    )
    list_filter = ("app_label", "view_name", "action", "status_code")
    search_fields = ("view_name", "path", "model_label", "metadata")
    readonly_fields = (
        "timestamp",
        "user",
        "app_label",
        "view_name",
        "path",
        "method",
        "status_code",
        "model_label",
        "action",
        "metadata",
    )
    ordering = ("-timestamp",)

    def changelist_view(self, request, extra_context=None):
        """Show stored analytics even when collection is currently paused."""

        if usage_analytics_collection_paused():
            self.message_user(
                request,
                "Usage analytics collection is disabled. Existing analytics remain viewable.",
                level=messages.WARNING,
            )
        return super().changelist_view(request, extra_context=extra_context)

    def has_add_permission(self, request):  # pragma: no cover - admin UX
        return False
