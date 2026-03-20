from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.chats.models import ChatAvatar
from apps.core.admin import OwnableAdminMixin


@admin.register(ChatAvatar)
class ChatAvatarAdmin(OwnableAdminMixin, admin.ModelAdmin):
    """Admin configuration for chat avatars."""

    list_display = ("name", "owner_display", "is_enabled")
    search_fields = ("name", "user__username", "group__name")
    list_filter = ("is_enabled",)
    inlines = []

    @admin.display(description=_("Owner"))
    def owner_display(self, obj):
        """Return the owner label shown for a chat avatar in admin lists.

        Parameters:
            obj (ChatAvatar): Avatar instance being rendered in the changelist.

        Returns:
            str: Owner display text returned by the avatar instance.
        """

        return obj.owner_display()
