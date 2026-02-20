from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin
from apps.meta.models import WhatsAppWebhook, WhatsAppWebhookMessage


@admin.register(WhatsAppWebhook)
class WhatsAppWebhookAdmin(EntityModelAdmin):
    list_display = ("bridge", "route_key", "webhook_url_preview")
    readonly_fields = (
        "webhook_path_preview",
        "webhook_url_preview",
        "verification_instructions",
        "is_seed_data",
        "is_user_data",
        "is_deleted",
    )
    search_fields = ("bridge__phone_number_id", "route_key", "bridge__site__domain")

    @admin.display(description=_("Webhook path"))
    def webhook_path_preview(self, obj):
        if not obj.pk:
            return _("Save first to generate the webhook route.")
        return obj.webhook_path()

    @admin.display(description=_("Webhook URL"))
    def webhook_url_preview(self, obj):
        if not obj.pk:
            return _("Save first to generate the webhook URL.")
        return obj.webhook_url()

    @admin.display(description=_("Setup help"))
    def verification_instructions(self, obj):
        if not obj.pk:
            return _("Save this webhook first to reveal the values to configure in Meta.")
        return format_html(
            "<strong>{}</strong><br>{}<code>{}</code><br>{}<code>{}</code><br>{}",
            _("Meta webhook setup values"),
            _("Callback URL: "),
            obj.webhook_url(),
            _("Verify token: "),
            obj.verify_token,
            _(
                "In Meta App Dashboard go to WhatsApp > Configuration and paste these values in the webhook section."
            ),
        )

    fieldsets = (
        (None, {"fields": ("bridge", "route_key", "verify_token")}),
        (
            _("Webhook configuration guidance"),
            {
                "fields": (
                    "webhook_path_preview",
                    "webhook_url_preview",
                    "verification_instructions",
                )
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


@admin.register(WhatsAppWebhookMessage)
class WhatsAppWebhookMessageAdmin(EntityModelAdmin):
    list_display = (
        "message_id",
        "webhook",
        "from_phone",
        "wa_id",
        "message_type",
        "timestamp",
    )
    list_filter = ("message_type", "webhook")
    search_fields = ("message_id", "from_phone", "wa_id", "text_body")
    readonly_fields = (
        "webhook",
        "message_id",
        "messaging_product",
        "from_phone",
        "wa_id",
        "profile_name",
        "timestamp",
        "message_type",
        "text_body",
        "context_message_id",
        "metadata_phone_number_id",
        "metadata_display_phone_number",
        "payload",
        "is_seed_data",
        "is_user_data",
        "is_deleted",
    )


__all__ = ["admin"]
