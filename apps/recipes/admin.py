from django.contrib import admin

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin
from apps.recipes.models import Recipe, RecipeStep


class RecipeStepInline(admin.TabularInline):
    model = RecipeStep
    fields = ("order", "command")
    extra = 1
    ordering = ("order",)


@admin.register(Recipe)
class RecipeAdmin(OwnableAdminMixin, EntityModelAdmin):
    list_display = ("display", "slug", "uuid", "owner", "updated_at")
    search_fields = ("display", "slug", "uuid")
    readonly_fields = ("uuid", "created_at", "updated_at")
    inlines = (RecipeStepInline,)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "display",
                    "slug",
                    "uuid",
                    "result_variable",
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
