"""Admin configuration for Evergo integration."""

from django.contrib import admin, messages

from apps.core.admin import OwnableAdminMixin

from .exceptions import EvergoAPIError
from .models import EvergoUser


@admin.register(EvergoUser)
class EvergoUserAdmin(OwnableAdminMixin, admin.ModelAdmin):
    """Manage Evergo users and allow login verification from admin actions."""

    list_display = (
        "id",
        "owner_display",
        "name",
        "email",
        "evergo_user_id",
        "empresa_id",
        "subempresa_id",
        "two_fa_enabled",
        "last_login_test_at",
    )
    search_fields = ("name", "email", "evergo_email", "evergo_user_id")
    list_filter = ("two_fa_enabled", "two_fa_authenticated", "created_at", "updated_at")
    readonly_fields = (
        "evergo_user_id",
        "name",
        "email",
        "empresa_id",
        "empresa_name",
        "subempresa_id",
        "subempresa_name",
        "two_fa_enabled",
        "two_fa_authenticated",
        "two_factor_secret",
        "two_factor_recovery_codes",
        "two_factor_confirmed_at",
        "evergo_created_at",
        "evergo_updated_at",
        "last_login_test_at",
        "created_at",
        "updated_at",
    )
    actions = ("test_login_and_sync",)
    fieldsets = (
        (
            "Ownership",
            {
                "fields": ("user", "group", "avatar"),
            },
        ),
        (
            "Credentials",
            {
                "fields": ("evergo_email", "evergo_password"),
            },
        ),
        (
            "Evergo synced profile",
            {
                "fields": (
                    "evergo_user_id",
                    "name",
                    "email",
                    "empresa_id",
                    "empresa_name",
                    "subempresa_id",
                    "subempresa_name",
                ),
            },
        ),
        (
            "Two-factor",
            {
                "fields": (
                    "two_fa_enabled",
                    "two_fa_authenticated",
                    "two_factor_confirmed_at",
                    "two_factor_secret",
                    "two_factor_recovery_codes",
                ),
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "evergo_created_at",
                    "evergo_updated_at",
                    "last_login_test_at",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    @admin.action(description="Test Evergo login and sync profile fields")
    def test_login_and_sync(self, request, queryset):
        """Call the Evergo API for selected records and persist returned metadata."""
        succeeded = 0
        for profile in queryset:
            try:
                profile.test_login()
            except EvergoAPIError as exc:
                self.message_user(
                    request,
                    f"Evergo login failed for {profile}: {exc}",
                    level=messages.ERROR,
                )
            else:
                succeeded += 1
        if succeeded:
            self.message_user(
                request,
                f"Evergo login succeeded for {succeeded} profile(s).",
                level=messages.SUCCESS,
            )
