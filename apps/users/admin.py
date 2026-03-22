"""Admin registrations for the users app."""

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms import ModelForm
from django.utils.translation import gettext_lazy as _

from apps.core.admin.mixins import OwnableAdminForm, OwnableAdminMixin

from .models import ChatProfile, UserFlag


class ChatProfileAdminForm(OwnableAdminForm):
    """Admin form that supports avatar ownership alongside user/group owners."""

    def clean(self):
        cleaned_data = ModelForm.clean(self)

        user = cleaned_data.get("user")
        group = cleaned_data.get("group")
        avatar = cleaned_data.get("avatar")
        owner_count = sum(bool(owner) for owner in (user, group, avatar))

        if owner_count > 1:
            raise ValidationError(
                _("A chat profile must have exactly one owner (user, group, or avatar).")
            )

        owner_required = getattr(self._meta.model, "owner_required", True)
        if owner_required and owner_count == 0:
            raise ValidationError(
                _("A chat profile must be assigned to a user, group, or avatar.")
            )

        return cleaned_data


@admin.register(ChatProfile)
class ChatProfileAdmin(OwnableAdminMixin, admin.ModelAdmin):
    """Manage per-owner chat preferences."""

    form = ChatProfileAdminForm

    list_display = (
        "id",
        "owner_display",
        "contact_via_chat",
        "is_enabled",
    )
    list_filter = ("contact_via_chat", "is_enabled")
    search_fields = ("user__username", "group__name", "avatar__name")


@admin.register(UserFlag)
class UserFlagAdmin(admin.ModelAdmin):
    """Manage user-level flags that apply independently of avatars."""

    list_display = ("id", "user", "key", "is_enabled", "updated_at")
    list_filter = ("is_enabled",)
    search_fields = ("user__username", "user__email", "key")


__all__ = ["admin"]
