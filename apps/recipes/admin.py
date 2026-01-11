from django.contrib import admin

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin
from apps.recipes.models import Recipe


@admin.register(Recipe)
class RecipeAdmin(OwnableAdminMixin, EntityModelAdmin):
    list_display = ("display", "slug", "uuid", "owner", "updated_at")
    search_fields = ("display", "slug", "uuid")
    readonly_fields = ("uuid", "created_at", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "display",
                    "slug",
                    "uuid",
                    "result_variable",
                    "script",
                )
            },
        ),
        (
            "Ownership",
            {
                "fields": (
                    "user",
                    "group",
                )
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at")},
        ),
    )
