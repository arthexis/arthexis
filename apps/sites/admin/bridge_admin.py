from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin
from apps.meta.models import WhatsAppChatBridge
from apps.odoo.models import OdooChatBridge


@admin.register(OdooChatBridge)
class OdooChatBridgeAdmin(EntityModelAdmin):
    list_display = ("bridge_label", "site", "channel_id", "is_enabled", "is_default")
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

    @admin.display(description=_("Bridge"))
    def bridge_label(self, obj):
        return str(obj)


@admin.register(WhatsAppChatBridge)
class WhatsAppChatBridgeAdmin(EntityModelAdmin):
    list_display = (
        "bridge_label",
        "site",
        "phone_number_id",
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
