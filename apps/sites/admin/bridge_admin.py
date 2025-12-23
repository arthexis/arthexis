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
    readonly_fields = ("is_seed_data", "is_user_data", "is_deleted")
    fieldsets = (
        (None, {"fields": ("site", "is_default", "is_enabled")}),
        (
            _("WhatsApp client"),
            {"fields": ("api_base_url", "phone_number_id", "access_token")},
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
