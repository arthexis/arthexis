"""Admin configuration for Evergo integration."""

from django.contrib import admin, messages

from .exceptions import EvergoAPIError
from .models import EvergoUser


@admin.register(EvergoUser)
class EvergoUserAdmin(admin.ModelAdmin):
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
    readonly_fields = ("created_at", "updated_at", "last_login_test_at")
    actions = ("test_login_and_sync",)

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
