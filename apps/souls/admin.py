from django.contrib import admin

from .models import (
    AgentInterfaceSpec,
    CardSession,
    ShopOrderSoulAttachment,
    SkillBundle,
    Soul,
    SoulIntent,
    SoulRegistrationSession,
    SoulSeedCard,
)


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


@admin.register(SoulIntent)
class SoulIntentAdmin(admin.ModelAdmin):
    list_display = ("id", "role", "risk_level", "desired_interface", "created_by", "created_at")
    list_filter = ("risk_level", "desired_interface", "role")
    search_fields = ("problem_statement", "normalized_intent", "tags")


@admin.register(SkillBundle)
class SkillBundleAdmin(admin.ModelAdmin):
    list_display = ("slug", "match_strategy", "match_score", "primary_skill", "intent")
    list_filter = ("match_strategy",)
    search_fields = ("slug", "name", "summary", "fallback_guidance")
    filter_horizontal = ("skills",)


@admin.register(AgentInterfaceSpec)
class AgentInterfaceSpecAdmin(admin.ModelAdmin):
    list_display = ("id", "bundle", "mode", "updated_at")
    list_filter = ("mode",)
    search_fields = ("bundle__slug",)


@admin.register(SoulSeedCard)
class SoulSeedCardAdmin(admin.ModelAdmin):
    list_display = ("card_uid", "status", "skill_bundle", "interface_spec", "owner", "updated_at")
    list_filter = ("status",)
    search_fields = ("card_uid", "manifest_fingerprint", "skill_bundle__slug", "owner__username")


@admin.register(CardSession)
class CardSessionAdmin(admin.ModelAdmin):
    list_display = ("session_id", "state", "card", "reader_id", "node_id", "trust_tier", "last_seen_at")
    list_filter = ("state", "trust_tier")
    search_fields = ("session_id", "reader_id", "node_id", "card__card_uid")
