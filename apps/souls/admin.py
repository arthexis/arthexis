from django.contrib import admin

from .models import ShopOrderSoulAttachment, Soul, SoulRegistrationSession


@admin.register(SoulRegistrationSession)
class SoulRegistrationSessionAdmin(admin.ModelAdmin):
    list_display = ("email", "state", "offering_soul", "survey_response", "verification_sent_at")
    search_fields = ("email",)


@admin.register(Soul)
class SoulAdmin(admin.ModelAdmin):
    list_display = ("soul_id", "user", "offering_soul", "survey_response", "email_verified_at")
    search_fields = ("soul_id", "user__username", "user__email")


@admin.register(ShopOrderSoulAttachment)
class ShopOrderSoulAttachmentAdmin(admin.ModelAdmin):
    list_display = ("order_item", "soul", "status")
    search_fields = ("order_item__order__order_number", "soul__soul_id")
