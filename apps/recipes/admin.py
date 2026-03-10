from django import forms
from django.contrib import admin
from django.db import models

from apps.core.admin import OwnableAdminMixin
from apps.locals.entity import EntityModelAdmin
from apps.recipes.models import Recipe, RecipeProduct


@admin.register(Recipe)
class RecipeAdmin(OwnableAdminMixin, EntityModelAdmin):
    """Admin configuration for recipe management."""

    list_display = ("display", "slug", "body_type", "uuid", "owner", "updated_at")
    search_fields = ("display", "slug", "uuid")
    readonly_fields = ("uuid", "created_at", "updated_at")
    formfield_overrides = {
        models.TextField: {
            "widget": forms.Textarea(
                attrs={
                    "style": (
                        "font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "
                        "'Liberation Mono', 'Courier New', monospace;"
                    )
                }
            )
        }
    }
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "display",
                    "slug",
                    "uuid",
                    "body_type",
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


@admin.register(RecipeProduct)
class RecipeProductAdmin(admin.ModelAdmin):
    """Read-only admin view for persisted recipe execution artifacts."""

    list_display = ("recipe", "format_detected", "executed_at")
    search_fields = ("recipe__slug", "recipe__display", "format_detected", "result")
    list_filter = ("format_detected", "executed_at")
    readonly_fields = (
        "recipe",
        "format_detected",
        "input_args",
        "input_kwargs",
        "result",
        "result_variable",
        "resolved_script",
        "executed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
