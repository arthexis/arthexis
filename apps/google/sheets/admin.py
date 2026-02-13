"""Admin registrations for Google Drive accounts and Google Sheets."""

from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django_object_actions import DjangoObjectActions

from apps.locals.user_data import EntityModelAdmin

from .models import DriveAccount, GoogleSheet
from .services import load_sheet_headers


@admin.register(DriveAccount)
class DriveAccountAdmin(EntityModelAdmin):
    """Manage modelled Google Drive accounts."""

    list_display = ("name", "email", "masked_access_token", "masked_refresh_token")
    search_fields = ("name", "email")
    readonly_fields = ("masked_access_token", "masked_refresh_token")
    exclude = ("access_token", "refresh_token")

    @staticmethod
    def _mask_token(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "•" * len(value)
        return f"{value[:4]}…{value[-4:]}"

    def masked_access_token(self, obj: DriveAccount) -> str:
        """Show a redacted preview of the access token."""

        return self._mask_token(obj.access_token)

    masked_access_token.short_description = "Access token"

    def masked_refresh_token(self, obj: DriveAccount) -> str:
        """Show a redacted preview of the refresh token."""

        return self._mask_token(obj.refresh_token)

    masked_refresh_token.short_description = "Refresh token"


@admin.register(GoogleSheet)
class GoogleSheetAdmin(DjangoObjectActions, EntityModelAdmin):
    """Manage tracked Google Sheets and load headers directly from admin."""

    list_display = (
        "title",
        "spreadsheet_id",
        "drive_account",
        "is_public",
        "last_loaded_at",
    )
    search_fields = ("title", "spreadsheet_id", "sheet_url", "drive_account__email")
    list_filter = ("is_public", "drive_account")
    change_actions = ("load_headers_action",)
    changelist_actions = ("discover_from_url",)
    change_list_template = "django_object_actions/change_list.html"

    readonly_fields = ("headers", "sheet_metadata", "last_loaded_at")

    def save_model(self, request, obj, form, change):
        """Allow creating sheet records using only a URL."""

        if obj.sheet_url and not obj.spreadsheet_id:
            obj.spreadsheet_id = GoogleSheet.spreadsheet_id_from_url(obj.sheet_url)
        super().save_model(request, obj, form, change)

    @admin.action(description="Load Headers")
    def load_headers_action(self, request, obj: GoogleSheet):
        """Fetch and persist first-row headers and sheet summary metadata."""

        try:
            result = load_sheet_headers(obj)
            obj.set_loaded_data(result)
        except ValidationError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return
        except Exception as exc:  # noqa: BLE001
            self.message_user(
                request,
                f"Unable to load headers for sheet {obj.spreadsheet_id}: {exc}",
                level=messages.ERROR,
            )
            return

        self.message_user(
            request,
            f"Loaded {len(obj.headers)} headers from {obj.worksheet_title or 'Sheet1'}.",
            level=messages.SUCCESS,
        )

    load_headers_action.label = "Load Headers"

    @admin.action(description="Discover from URL")
    def discover_from_url(self, request, queryset=None):
        """Open the URL-based discovery tool for adding tracked sheets."""

        return redirect(reverse("google:sheets-discover"))

    discover_from_url.label = "Discover from URL"
    discover_from_url.short_description = "Discover from URL"
    discover_from_url.changelist = True
