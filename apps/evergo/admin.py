"""Admin configuration for Evergo integration."""

from django.contrib import admin, messages
from django.shortcuts import render
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.core.admin import OwnableAdminMixin, SaveBeforeChangeAction
from apps.core.admin.mixins import _build_credentials_actions

from .exceptions import EvergoAPIError
from .forms import EvergoLoadCustomersForm
from .models import EvergoCustomer, EvergoOrder, EvergoOrderFieldValue, EvergoUser


@admin.register(EvergoUser)
class EvergoUserAdmin(
    SaveBeforeChangeAction, OwnableAdminMixin, DjangoObjectActions, admin.ModelAdmin
):
    """Manage Evergo users and allow login verification from admin actions."""

    change_form_template = "django_object_actions/change_form.html"

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
    actions = ("_test_login_and_sync_bulk_action", "load_orders")
    changelist_actions = ("load_customers_wizard",)
    change_actions = ("test_login_and_sync_action",)
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

    def _test_login_and_sync(self, request, profile):
        """Call the Evergo API and persist synchronized user metadata."""
        try:
            profile.test_login()
        except EvergoAPIError as exc:
            self.message_user(
                request,
                f"Evergo login failed for {profile}: {exc}",
                level=messages.ERROR,
            )
            return False
        self.message_user(
            request,
            f"Evergo login succeeded for {profile}.",
            level=messages.SUCCESS,
        )
        return True

    def get_urls(self):
        """Register custom admin routes for Evergo tools."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "load-customers/",
                self.admin_site.admin_view(self.load_customers_view),
                name="evergo_evergouser_load_customers",
            )
        ]
        return custom_urls + urls

    def load_orders(self, request, queryset=None):
        """Load orders from Evergo for selected users or for current profile from changelist tool."""
        if queryset is None or not queryset.exists():
            fallback_queryset = self.get_queryset(request).filter(user=request.user)
            queryset = fallback_queryset

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

        self.message_user(
            request,
            _("Evergo orders sync completed. Created: %(created)s Updated: %(updated)s")
            % {"created": total_created, "updated": total_updated},
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:evergo_evergouser_changelist"))

    load_orders.label = _("Load Orders")
    load_orders.short_description = _("Load Orders")
    load_orders.requires_queryset = False

    def load_customers_wizard(self, request, queryset=None):
        """Redirect changelist action to the customer-loading wizard."""
        return HttpResponseRedirect(reverse("admin:evergo_evergouser_load_customers"))

    load_customers_wizard.label = _("Load Customers")
    load_customers_wizard.short_description = _("Load Customers")
    load_customers_wizard.requires_queryset = False

    def load_customers_view(self, request):
        """Render/handle the SO-customer import wizard."""
        if request.method == "POST":
            form = EvergoLoadCustomersForm(request.POST, request_user=request.user)
            if form.is_valid():
                profile = form.cleaned_data["profile"]
                raw_queries = form.cleaned_data["raw_queries"]
                try:
                    summary = profile.load_customers_from_queries(raw_queries=raw_queries)
                except EvergoAPIError as exc:
                    self.message_user(
                        request,
                        _("Failed loading customers for %(profile)s: %(error)s")
                        % {"profile": str(profile), "error": exc},
                        level=messages.ERROR,
                    )
                    return HttpResponseRedirect(
                        reverse("admin:evergo_evergouser_load_customers")
                    )
                else:
                    self.message_user(
                        request,
                        _(
                            "Customer sync completed. Customers loaded: %(customers)s | "
                            "Orders created: %(created)s | Orders updated: %(updated)s | "
                            "Placeholders: %(placeholders)s"
                        )
                        % {
                            "customers": summary["customers_loaded"],
                            "created": summary["orders_created"],
                            "updated": summary["orders_updated"],
                            "placeholders": summary["placeholders_created"],
                        },
                        level=messages.SUCCESS,
                    )
                    if summary["unresolved"]:
                        self.message_user(
                            request,
                            _("Not found in Evergo: %(items)s")
                            % {"items": ", ".join(summary["unresolved"])},
                            level=messages.WARNING,
                        )
                    return HttpResponseRedirect(reverse("admin:evergo_evergouser_changelist"))
        else:
            form = EvergoLoadCustomersForm(request_user=request.user)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Load Evergo Customers"),
            "form": form,
            "add_profile_url": reverse("admin:evergo_evergouser_add"),
        }
        return render(request, "admin/evergo/load_customers.html", context)

    (
        _test_login_and_sync_bulk_action,
        test_login_and_sync_action,
    ) = _build_credentials_actions(
        "_test_login_and_sync_bulk_action",
        "_test_login_and_sync",
        _("Test Evergo login and sync profile fields"),
    )


@admin.register(EvergoOrder)
class EvergoOrderAdmin(DjangoObjectActions, admin.ModelAdmin):
    """Inspect synchronized Evergo order snapshots."""

    changelist_actions = ("load_orders_wizard",)

    list_display = (
        "remote_id",
        "order_number",
        "user",
        "status_name",
        "site_name",
        "assigned_engineer_name",
        "validation_state",
        "refreshed_at",
    )
    list_filter = (
        "validation_state",
        "status_name",
        "site_name",
        "has_charger",
        "has_vehicle",
    )
    search_fields = (
        "order_number",
        "remote_id",
        "client_name",
        "assigned_engineer_name",
        "assigned_coordinator_name",
    )
    readonly_fields = ("raw_payload", "validation_state", "refreshed_at", "created_at")

    def load_orders_wizard(self, request, queryset=None):
        """Redirect the order dashboard tool to the shared load wizard view."""
        return HttpResponseRedirect(reverse("admin:evergo_evergouser_load_customers"))

    load_orders_wizard.label = _("Load Orders")
    load_orders_wizard.short_description = _("Load Orders")
    load_orders_wizard.requires_queryset = False


@admin.register(EvergoOrderFieldValue)
class EvergoOrderFieldValueAdmin(admin.ModelAdmin):
    """Inspect learned dropdown catalog values from Evergo."""

    list_display = ("field_name", "remote_id", "remote_name", "local_label", "last_seen_at")
    list_filter = ("field_name",)
    search_fields = ("field_name", "remote_name", "local_label")
    readonly_fields = ("last_seen_at", "created_at", "raw_payload")


@admin.register(EvergoCustomer)
class EvergoCustomerAdmin(admin.ModelAdmin):
    """Inspect customer snapshots synchronized from Evergo orders."""

    list_display = ("latest_so", "name", "phone_number", "address", "user")
    list_filter = ("user",)
    search_fields = ("latest_so", "name", "phone_number", "address", "email")
    readonly_fields = ("raw_payload", "refreshed_at", "created_at")
