"""Admin configuration for Evergo integration."""

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.core.admin import OwnableAdminMixin

from .exceptions import EvergoAPIError
from .models import EvergoOrder, EvergoOrderFieldValue, EvergoUser


@admin.register(EvergoUser)
class EvergoUserAdmin(OwnableAdminMixin, DjangoObjectActions, admin.ModelAdmin):
    """Manage Evergo users and allow login verification from admin actions."""

    list_display = (
        "id",
        "owner_display",
        "evergo_email",
        "name",
        "email",
        "two_fa_enabled",
        "last_login_test_at",
    )
    search_fields = ("name", "email", "evergo_email", "evergo_user_id")
    list_filter = ("two_fa_enabled", "two_fa_authenticated", "created_at", "updated_at")
    readonly_fields = (
        "evergo_user_id",
        "name",
        "email",
        "empresa_id",
        "empresa_name",
        "subempresa_id",
        "subempresa_name",
        "two_fa_enabled",
        "two_fa_authenticated",
        "two_factor_secret",
        "two_factor_recovery_codes",
        "two_factor_confirmed_at",
        "evergo_created_at",
        "evergo_updated_at",
        "last_login_test_at",
        "created_at",
        "updated_at",
    )
    actions = ("test_login_and_sync", "load_orders")
    changelist_actions = ("load_orders",)
    fieldsets = (
        (
            "Ownership",
            {
                "fields": ("user", "group", "avatar"),
            },
        ),
        (
            "Credentials",
            {
                "fields": ("evergo_email", "evergo_password"),
            },
        ),
        (
            "Evergo synced profile",
            {
                "fields": (
                    "evergo_user_id",
                    "name",
                    "email",
                    "empresa_id",
                    "empresa_name",
                    "subempresa_id",
                    "subempresa_name",
                ),
            },
        ),
        (
            "Two-factor",
            {
                "fields": (
                    "two_fa_enabled",
                    "two_fa_authenticated",
                    "two_factor_confirmed_at",
                    "two_factor_secret",
                    "two_factor_recovery_codes",
                ),
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "evergo_created_at",
                    "evergo_updated_at",
                    "last_login_test_at",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    @admin.action(description="Test Evergo login and sync profile fields")
    def test_login_and_sync(self, request, queryset):
        """Call the Evergo API for selected records and persist returned metadata."""
        succeeded = 0
        for profile in queryset:
            try:
                profile.test_login()
            except EvergoAPIError as exc:
                self.message_user(
                    request,
                    f"Evergo login failed for {profile}: {exc}",
                    level=messages.ERROR,
                )
            else:
                succeeded += 1
        if succeeded:
            self.message_user(
                request,
                f"Evergo login succeeded for {succeeded} profile(s).",
                level=messages.SUCCESS,
            )

    def load_orders(self, request, queryset=None):
        """Load orders from Evergo for selected users or for current profile from changelist tool."""
        if queryset is None:
            queryset = self.get_queryset(request).filter(user=request.user)

        if not queryset.exists():
            self.message_user(
                request,
                _("No Evergo profiles selected to load orders."),
                level=messages.WARNING,
            )
            return HttpResponseRedirect(
                reverse("admin:evergo_evergouser_changelist")
            )

        total_created = 0
        total_updated = 0
        any_succeeded = False
        for profile in queryset:
            try:
                created, updated = profile.load_orders()
            except EvergoAPIError as exc:
                self.message_user(
                    request,
                    _("Failed loading orders for %(profile)s: %(error)s")
                    % {"profile": str(profile), "error": exc},
                    level=messages.ERROR,
                )
            else:
                total_created += created
                total_updated += updated
                any_succeeded = True

        if any_succeeded:
            self.message_user(
                request,
                _("Evergo orders sync completed. Created: %(created)s Updated: %(updated)s")
                % {"created": total_created, "updated": total_updated},
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("Evergo orders sync failed for all selected profiles."),
                level=messages.WARNING,
            )
        return HttpResponseRedirect(reverse("admin:evergo_evergouser_changelist"))

    load_orders.label = _("Load Orders")
    load_orders.short_description = _("Load Orders")
    load_orders.requires_queryset = False


@admin.register(EvergoOrder)
class EvergoOrderAdmin(admin.ModelAdmin):
    """Inspect synchronized Evergo order snapshots."""

    list_display = (
        "remote_id",
        "order_number",
        "user",
        "status_name",
        "site_name",
        "assigned_engineer_name",
        "refreshed_at",
    )
    list_filter = ("status_name", "site_name", "has_charger", "has_vehicle")
    search_fields = (
        "order_number",
        "remote_id",
        "client_name",
        "assigned_engineer_name",
        "assigned_coordinator_name",
    )
    readonly_fields = ("raw_payload", "refreshed_at", "created_at")


@admin.register(EvergoOrderFieldValue)
class EvergoOrderFieldValueAdmin(admin.ModelAdmin):
    """Inspect learned dropdown catalog values from Evergo."""

    list_display = ("field_name", "remote_id", "remote_name", "local_label", "last_seen_at")
    list_filter = ("field_name",)
    search_fields = ("field_name", "remote_name", "local_label")
    readonly_fields = ("last_seen_at", "created_at", "raw_payload")
