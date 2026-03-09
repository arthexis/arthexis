from datetime import datetime, timezone as dt_timezone
from django.contrib import admin
from django.db.models import Count, Max, Q
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin
from apps.meta.models import WhatsAppChatBridge
from apps.odoo.models import OdooChatBridge


@admin.register(OdooChatBridge)
class OdooChatBridgeAdmin(EntityModelAdmin):
    list_display = (
        "channel_id",
        "bridge_label",
        "site",
        "avatar_count",
        "last_used_at",
        "is_enabled",
        "is_default",
    )
    list_filter = ("is_enabled", "is_default", "site")
    search_fields = ("channel_uuid", "channel_id")
    ordering = ("site__domain", "channel_id")
    readonly_fields = ("is_seed_data", "is_user_data", "is_deleted")
    fieldsets = (
        (None, {"fields": ("site", "is_default", "profile", "is_enabled")}),
        (
            _("Odoo channel"),
            {"fields": ("channel_id", "channel_uuid", "notify_partner_ids")},
        ),
        (
            _("Flags"),
            {
                "fields": ("is_seed_data", "is_user_data", "is_deleted"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                total_avatars=Count("avatars", distinct=True),
                enabled_avatars=Count(
                    "avatars", filter=Q(avatars__is_enabled=True), distinct=True
                ),
            )
        )

    @admin.display(description=_("Avatars"), ordering="enabled_avatars")
    def avatar_count(self, obj):
        enabled = getattr(obj, "enabled_avatars", 0)
        total = getattr(obj, "total_avatars", 0)
        return f"{enabled}/{total}"

    @admin.display(description=_("Last used"))
    def last_used_at(self, obj):
        return "-"

    @admin.display(description=_("Bridge"))
    def bridge_label(self, obj):
        return str(obj)


@admin.register(WhatsAppChatBridge)
class WhatsAppChatBridgeAdmin(EntityModelAdmin):
    list_display = (
        "phone_number_id",
        "bridge_label",
        "site",
        "avatar_count",
        "last_used_at",
        "is_enabled",
        "is_default",
    )
    list_filter = ("is_enabled", "is_default", "site")
    search_fields = ("phone_number_id",)
    ordering = ("site__domain", "phone_number_id")
    readonly_fields = ("is_seed_data", "is_user_data", "is_deleted", "webhook_setup_help")
    fieldsets = (
        (None, {"fields": ("site", "is_default", "is_enabled")}),
        (
            _("WhatsApp client"),
            {"fields": ("api_base_url", "phone_number_id", "access_token")},
        ),
        (
            _("Inbound webhook"),
            {
                "fields": ("webhook_setup_help",),
                "description": _(
                    "Create a WhatsApp Webhook record for this bridge to receive inbound messages."
                ),
            },
        ),
        (
            _("Flags"),
            {
                "fields": ("is_seed_data", "is_user_data", "is_deleted"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                total_avatars=Count("avatars", distinct=True),
                enabled_avatars=Count(
                    "avatars", filter=Q(avatars__is_enabled=True), distinct=True
                ),
                last_webhook_timestamp=Max("webhook__messages__timestamp"),
            )
        )

    @admin.display(description=_("Avatars"), ordering="enabled_avatars")
    def avatar_count(self, obj):
        enabled = getattr(obj, "enabled_avatars", 0)
        total = getattr(obj, "total_avatars", 0)
        return f"{enabled}/{total}"

    @admin.display(description=_("Last used"), ordering="last_webhook_timestamp")
    def last_used_at(self, obj):
        timestamp = getattr(obj, "last_webhook_timestamp", None)
        if not timestamp:
            return "-"
        return datetime.fromtimestamp(timestamp, tz=dt_timezone.utc)

    @admin.display(description=_("Webhook"))
    def webhook_setup_help(self, obj):
        webhook = getattr(obj, "webhook", None)
        if webhook is None:
            return _(
                "No webhook configured yet. Add one in Meta > WhatsApp Webhooks using this bridge."
            )
        return _("Configured route key: %(route_key)s") % {"route_key": webhook.route_key}

    @admin.display(description=_("Bridge"))
    def bridge_label(self, obj):
        return str(obj)
