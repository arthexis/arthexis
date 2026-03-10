"""Admin registrations for game models."""

from django.contrib import admin

from .models import Avatar


@admin.register(Avatar)
class AvatarAdmin(admin.ModelAdmin):
    """Manage user avatars for game experiences."""

    list_display = ("id", "avatar_display", "user", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("nickname", "user__username", "user__email")

    @admin.display(description="Avatar")
    def avatar_display(self, obj: Avatar) -> str:
        """Return formatted avatar label for admin lists."""

        return str(obj)
