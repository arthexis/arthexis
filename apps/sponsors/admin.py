"""Admin configuration for sponsors."""

from django.contrib import admin

from .models import SponsorTier, Sponsorship, SponsorshipPayment


@admin.register(SponsorTier)
class SponsorTierAdmin(admin.ModelAdmin):
    list_display = ("name", "amount", "currency", "is_active")
    list_filter = ("is_active", "currency")
    search_fields = ("name",)
    filter_horizontal = ("security_groups",)


@admin.register(Sponsorship)
class SponsorshipAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "tier",
        "status",
        "renewal_mode",
        "started_at",
        "next_renewal_at",
    )
    list_filter = ("status", "renewal_mode")
    search_fields = ("user__username", "user__email")
    autocomplete_fields = ("user", "tier")


@admin.register(SponsorshipPayment)
class SponsorshipPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "sponsorship",
        "amount",
        "currency",
        "status",
        "kind",
        "processed_at",
    )
    list_filter = ("status", "kind", "currency")
    search_fields = ("external_reference",)
    autocomplete_fields = ("sponsorship",)
