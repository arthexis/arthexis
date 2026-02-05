from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin

from .models import Feature, FeatureNote, FeatureTest


class FeatureTestInline(admin.TabularInline):
    model = FeatureTest
    extra = 0
    fields = ("name", "node_id", "is_regression_guard", "notes")


class FeatureNoteInline(admin.TabularInline):
    model = FeatureNote
    extra = 0
    fields = ("author", "body", "updated_at")
    readonly_fields = ("updated_at",)


@admin.register(Feature)
class FeatureAdmin(OwnableAdminMixin, EntityModelAdmin):
    list_display = (
        "display",
        "slug",
        "is_enabled",
        "main_app",
        "node_feature",
        "owner_label",
    )
    list_filter = ("is_enabled", "main_app", "node_feature")
    search_fields = ("display", "slug", "summary")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "display",
                    "slug",
                    "summary",
                    "is_enabled",
                    "main_app",
                    "node_feature",
                )
            },
        ),
        (
            _("Ownership"),
            {"fields": ("user", "group")},
        ),
        (
            _("Feature surfaces"),
            {
                "fields": (
                    "admin_requirements",
                    "public_requirements",
                    "service_requirements",
                    "admin_views",
                    "public_views",
                    "service_views",
                )
            },
        ),
        (
            _("Coverage"),
            {
                "fields": (
                    "code_locations",
                    "protocol_coverage",
                )
            },
        ),
    )
    inlines = [FeatureNoteInline, FeatureTestInline]
