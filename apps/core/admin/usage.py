from django.contrib import admin

from apps.core.models import UsageEvent


@admin.register(UsageEvent)
class UsageEventAdmin(admin.ModelAdmin):
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

    def has_add_permission(self, request):  # pragma: no cover - admin UX
        return False
