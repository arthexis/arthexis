from django.contrib import admin

from apps.core.admin import OwnableAdminMixin
from apps.locals.admin_mixins import EntityModelAdmin

from .models import GoogleAccount, GoogleSheet, GoogleSheetColumn


@admin.register(GoogleAccount)
class GoogleAccountAdmin(OwnableAdminMixin, EntityModelAdmin):
    """Admin UI for Google OAuth account records."""

    list_display = ("email", "user", "group", "is_enabled")
    search_fields = ("email", "user__username", "group__name")
    list_filter = ("is_enabled",)


@admin.register(GoogleSheet)
class GoogleSheetAdmin(OwnableAdminMixin, EntityModelAdmin):
    """Admin UI for tracked Google Sheets."""

    list_display = ("name", "spreadsheet_id", "account", "default_worksheet", "is_enabled")
    search_fields = ("name", "spreadsheet_id", "default_worksheet", "account__email")
    list_filter = ("is_enabled",)
    autocomplete_fields = ("account",)


@admin.register(GoogleSheetColumn)
class GoogleSheetColumnAdmin(OwnableAdminMixin, EntityModelAdmin):
    """Admin UI for introspected sheet columns."""

    list_display = ("sheet", "worksheet", "name", "position", "detected_type")
    search_fields = ("sheet__name", "worksheet", "name")
    list_filter = ("detected_type", "worksheet")
    autocomplete_fields = ("sheet",)
