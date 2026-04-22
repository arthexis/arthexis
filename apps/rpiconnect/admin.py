"""Admin registrations for Raspberry Pi Connect integration models."""

from django import forms
from django.contrib import admin

from .models import (
    ConnectAccount,
    ConnectDevice,
    ConnectIngestionEvent,
    ConnectImageRelease,
    ConnectUpdateCampaign,
    ConnectUpdateDeployment,
)


class ConnectAccountAdminForm(forms.ModelForm):
    class Meta:
        model = ConnectAccount
        fields = "__all__"
        widgets = {
            "token_reference": forms.PasswordInput(render_value=True),
            "refresh_token_reference": forms.PasswordInput(render_value=True),
        }


@admin.register(ConnectAccount)
class ConnectAccountAdmin(admin.ModelAdmin):
    form = ConnectAccountAdminForm
    list_display = ("name", "account_type", "organization_name", "owner_email")
    list_filter = ("account_type", "created_at")
    search_fields = ("name", "organization_name", "owner_name", "owner_email")


@admin.register(ConnectDevice)
class ConnectDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "device_id",
        "account",
        "hardware_model",
        "os_release",
        "is_online",
        "last_seen",
        "enrollment_source",
    )
    list_filter = ("is_online", "enrollment_source", "hardware_model")
    search_fields = ("device_id", "hardware_model", "os_release", "account__name")


@admin.register(ConnectImageRelease)
class ConnectImageReleaseAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "checksum", "released_at")
    list_filter = ("released_at",)
    search_fields = ("name", "version", "checksum")


@admin.register(ConnectUpdateCampaign)
class ConnectUpdateCampaignAdmin(admin.ModelAdmin):
    list_display = ("id", "release", "strategy", "status", "created_by", "started_at", "completed_at")
    list_filter = ("strategy", "status", "started_at", "completed_at")
    search_fields = ("id", "release__name")


@admin.register(ConnectUpdateDeployment)
class ConnectUpdateDeploymentAdmin(admin.ModelAdmin):
    list_display = (
        "campaign",
        "device",
        "status",
        "failure_classification",
        "retry_attempts",
        "next_retry_at",
        "started_at",
        "completed_at",
    )
    list_filter = ("status", "failure_classification", "started_at", "completed_at")
    search_fields = ("device__device_id", "campaign__id", "campaign__release__name")


@admin.register(ConnectIngestionEvent)
class ConnectIngestionEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_id",
        "event_type",
        "external_device_id",
        "status",
        "failure_classification",
        "retry_attempt",
        "cooldown_until",
        "created_at",
    )
    list_filter = ("event_type", "failure_classification", "status")
    readonly_fields = ("payload_snippet", "normalized_payload")
    search_fields = ("event_id", "external_device_id", "deployment__id", "campaign__id")
