"""Admin registrations for Fitbit integration models."""

from django.contrib import admin

from apps.fitbit.models import FitbitConnection, FitbitHealthSample, FitbitNetMessageDelivery


@admin.register(FitbitConnection)
class FitbitConnectionAdmin(admin.ModelAdmin):
    """Admin for managing Fitbit account/device connections."""

    list_display = ("name", "fitbit_user_id", "device_id", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "fitbit_user_id", "device_id")


@admin.register(FitbitHealthSample)
class FitbitHealthSampleAdmin(admin.ModelAdmin):
    """Admin for stored Fitbit health history records."""

    list_display = ("connection", "resource", "observed_at", "polled_at")
    list_filter = ("resource",)
    search_fields = ("connection__name", "resource")


@admin.register(FitbitNetMessageDelivery)
class FitbitNetMessageDeliveryAdmin(admin.ModelAdmin):
    """Admin for Fitbit Net Message delivery events."""

    list_display = ("connection", "net_message", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("connection__name", "rendered_text")
