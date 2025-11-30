from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from apps.core.admin import EntityModelAdmin

from .models import BadgeCounter, DashboardRule


@admin.register(BadgeCounter)
class BadgeCounterAdmin(admin.ModelAdmin):
    list_display = ("name", "content_type", "priority", "is_enabled")
    list_filter = ("is_enabled", "content_type__app_label")
    search_fields = (
        "name",
        "content_type__app_label",
        "content_type__model",
    )
    ordering = ("priority", "name")
    fieldsets = (
        (_("Badge"), {"fields": ("name", "content_type", "priority", "is_enabled")}),
        (
            _("Display"),
            {"fields": ("css_class", "separator", "label_template")},
        ),
        (
            _("Values"),
            {
                "fields": (
                    "primary_source_type",
                    "primary_source",
                    "secondary_source_type",
                    "secondary_source",
                )
            },
        ),
    )
    actions = ["invalidate_cache"]

    @admin.action(description=_("Invalidate cached badge counters"))
    def invalidate_cache(self, request, queryset):
        content_types = set(queryset.values_list("content_type", flat=True))
        for content_type_id in content_types:
            BadgeCounter.invalidate_model_cache(
                ContentType.objects.filter(pk=content_type_id).first()
            )
        self.message_user(request, _("Badge counter cache cleared."))


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
