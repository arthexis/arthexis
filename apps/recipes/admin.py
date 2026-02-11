from django import forms
from django.contrib import admin
from django.db import models

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin
from apps.recipes.models import Recipe


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
