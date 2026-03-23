from django.contrib import admin
from django.forms import PasswordInput

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin

from .models import GoogleAccount, GoogleCalendar


@admin.register(GoogleAccount)
class GoogleAccountAdmin(OwnableAdminMixin, EntityModelAdmin):
    """Admin UI for Google OAuth account records used by calendar publishing."""

    list_display = ("email", "user", "group", "is_enabled")
    search_fields = ("email", "user__username", "group__name")
    list_filter = ("is_enabled",)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Mask OAuth credential values in the admin form.

        Parameters:
            db_field: Database field being rendered by the admin form.
            request: Active admin request object.
            **kwargs: Additional widget overrides passed to the parent admin.

        Returns:
            Field: The configured Django form field for the admin form.
        """
        if db_field.name in {
            "client_id",
            "client_secret",
            "refresh_token",
            "access_token",
        }:
            kwargs["widget"] = PasswordInput(render_value=True)
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(GoogleCalendar)
class GoogleCalendarAdmin(EntityModelAdmin):
    """Admin configuration for outbound Google Calendar destinations.

    Attributes:
        list_display: Core destination fields shown in the change list.
        list_filter: Filters for enabled state and timezone.
        search_fields: Lookup fields for destination and account identity.
        autocomplete_fields: Related account selector for large account tables.
    """

    list_display = ("name", "calendar_id", "account", "timezone", "is_enabled")
    list_filter = ("is_enabled", "timezone")
    search_fields = ("name", "calendar_id", "account__email")
    autocomplete_fields = ("account",)
