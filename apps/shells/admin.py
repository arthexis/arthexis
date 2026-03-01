"""Admin registration for shell script inventory models."""

from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin

from .models import AppShellScript, BaseShellScript


@admin.register(BaseShellScript)
class BaseShellScriptAdmin(EntityModelAdmin):
    """Expose base shell scripts in Django admin for auditing."""

    list_display = ("name", "path")
    search_fields = ("name", "path")


@admin.register(AppShellScript)
class AppShellScriptAdmin(EntityModelAdmin):
    """Expose app shell scripts in Django admin for auditing."""

    list_display = ("name", "path", "managed_by")
    list_filter = ("managed_by",)
    search_fields = ("name", "path", "managed_by__name")
    autocomplete_fields = ("managed_by",)
