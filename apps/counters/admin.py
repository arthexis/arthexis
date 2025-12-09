from django.contrib import admin, messages
from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.core.admin import EntityModelAdmin

from .models import DashboardRule


@admin.register(DashboardRule)
class DashboardRuleAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "content_type",
        "implementation",
        "function_name",
    )
    list_filter = ("implementation",)
    search_fields = (
        "name",
        "content_type__app_label",
        "content_type__model",
    )
    list_select_related = ("content_type",)
    raw_id_fields = ("content_type",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "content_type",
                    "implementation",
                )
            },
        ),
        (
            "Condition",
            {
                "fields": (
                    "condition",
                    "success_message",
                    "failure_message",
                ),
                "classes": ("wide",),
            },
        ),
        (
            "Python handler",
            {
                "fields": ("function_name",),
            },
        ),
    )

    def message_user(self, request, message, level=messages.INFO, extra_tags="", fail_silently=False):
        # Maintain consistent messaging behavior with EntityModelAdmin
        return super().message_user(request, message, level=level, extra_tags=extra_tags, fail_silently=fail_silently)
