from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.admin import OwnableAdminMixin

from .models import (
    AlexaAccount,
    AlexaCredentialsError,
    AlexaReminder,
    AlexaReminderDelivery,
)


class AlexaReminderDeliveryInline(admin.TabularInline):
    """Inline delivery status for reminder/account mappings."""

    model = AlexaReminderDelivery
    extra = 0
    autocomplete_fields = ("account",)
    readonly_fields = ("updated_at",)


class AlexaAccountAdminForm(forms.ModelForm):
    """Mask credential values by default and allow explicit reveal in the browser."""

    class Meta:
        model = AlexaAccount
        fields = "__all__"
        widgets = {
            "client_id": forms.PasswordInput(render_value=True),
            "client_secret": forms.PasswordInput(render_value=True),
            "refresh_token": forms.PasswordInput(render_value=True),
        }


@admin.register(AlexaAccount)
class AlexaAccountAdmin(OwnableAdminMixin, admin.ModelAdmin):
    """Admin for Alexa account credentials with a credential test action."""

    form = AlexaAccountAdminForm

    fieldsets = (
        (None, {"fields": ("name", "is_active")}),
        (
            _("Credentials"),
            {"fields": ("client_id", "client_secret", "refresh_token", "api_base_url")},
        ),
        (
            _("Credential health"),
            {"fields": ("last_credentials_check_at", "last_credentials_check_message")},
        ),
        (OwnableAdminMixin.ownable_fieldset[0], OwnableAdminMixin.ownable_fieldset[1]),
    )
    list_display = (
        "name",
        "owner_display",
        "is_active",
        "last_credentials_check_at",
        "last_credentials_check_message",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "user__username", "group__name")
    autocomplete_fields = ("user", "group")
    readonly_fields = ("last_credentials_check_at", "last_credentials_check_message")
    actions = ("test_credentials",)

    @admin.action(description=_("Test selected credentials"))
    def test_credentials(self, request, queryset):
        """Validate selected credential sets and persist check results."""
        for account in queryset:
            try:
                account.validate_credentials()
            except AlexaCredentialsError as exc:
                account.last_credentials_check_message = str(exc)
                self.message_user(
                    request,
                    _("%(account)s: %(error)s")
                    % {"account": account.name, "error": exc},
                    level=messages.ERROR,
                )
            else:
                account.last_credentials_check_message = str(
                    _("Credential values look valid."),
                )
                self.message_user(
                    request,
                    _("%(account)s: credentials look valid.")
                    % {"account": account.name},
                    level=messages.SUCCESS,
                )
            account.last_credentials_check_at = timezone.now()
            account.save(
                update_fields=(
                    "last_credentials_check_at",
                    "last_credentials_check_message",
                )
            )


@admin.register(AlexaReminder)
class AlexaReminderAdmin(OwnableAdminMixin, admin.ModelAdmin):
    """Admin for reminders and their per-account delivery state."""

    list_display = (
        "name",
        "event_key",
        "owner_display",
        "scheduled_for",
        "auto_update_on_event",
    )
    search_fields = ("name", "event_key", "content")
    list_filter = ("auto_update_on_event",)
    autocomplete_fields = ("user", "group")
    inlines = (AlexaReminderDeliveryInline,)

    def save_model(self, request, obj, form, change):
        """Persist reminders and mark sent deliveries for update when changed."""
        should_mark_updates = False
        if change and obj.pk and obj.auto_update_on_event:
            monitored_fields = {"content", "scheduled_for", "event_key"}
            should_mark_updates = bool(monitored_fields & set(form.changed_data))
        super().save_model(request, obj, form, change)
        if should_mark_updates:
            updated = obj.mark_event_update()
            if updated:
                self.message_user(
                    request,
                    _("Marked %(count)s delivery records for reminder updates.")
                    % {"count": updated},
                    level=messages.INFO,
                )


@admin.register(AlexaReminderDelivery)
class AlexaReminderDeliveryAdmin(admin.ModelAdmin):
    """Admin for direct visibility into delivery states."""

    list_display = ("reminder", "account", "status", "last_sent_at", "updated_at")
    search_fields = ("reminder__name", "account__name", "remote_reference", "last_error")
    list_filter = ("status",)
    autocomplete_fields = ("reminder", "account")
    readonly_fields = ("updated_at",)
