from django import forms
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.chats.models import ChatAvatar
from apps.core.admin import OwnableAdminMixin, ProfileFormMixin, ProfileInlineFormSet
from apps.socials.models import BlueskyProfile, DiscordProfile


class BlueskyProfileInlineForm(ProfileFormMixin, forms.ModelForm):
    profile_fields = BlueskyProfile.profile_fields

    class Meta:
        model = BlueskyProfile
        fields = ("handle", "domain", "did")


class DiscordProfileInlineForm(ProfileFormMixin, forms.ModelForm):
    profile_fields = DiscordProfile.profile_fields

    class Meta:
        model = DiscordProfile
        fields = (
            "application_id",
            "public_key",
            "guild_id",
            "bot_token",
            "default_channel_id",
        )


class BlueskyProfileInline(admin.StackedInline):
    model = BlueskyProfile
    form = BlueskyProfileInlineForm
    formset = ProfileInlineFormSet
    fk_name = "avatar"
    extra = 1
    max_num = 1
    can_delete = True
    verbose_name = _("Bluesky Identity")
    verbose_name_plural = _("Bluesky Identities")
    template = "admin/edit_inline/profile_stacked.html"


class DiscordProfileInline(admin.StackedInline):
    model = DiscordProfile
    form = DiscordProfileInlineForm
    formset = ProfileInlineFormSet
    fk_name = "avatar"
    extra = 1
    max_num = 1
    can_delete = True
    verbose_name = _("Discord Identity")
    verbose_name_plural = _("Discord Identities")
    template = "admin/edit_inline/profile_stacked.html"


@admin.register(ChatAvatar)
class ChatAvatarAdmin(OwnableAdminMixin, admin.ModelAdmin):
    list_display = ("name", "owner_display", "is_enabled")
    search_fields = ("name", "user__username", "group__name")
    list_filter = ("is_enabled",)
    inlines = [BlueskyProfileInline, DiscordProfileInline]

    @admin.display(description=_("Owner"))
    def owner_display(self, obj):
        return obj.owner_display()
