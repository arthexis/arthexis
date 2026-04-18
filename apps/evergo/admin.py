"""Admin configuration for Evergo integration."""

import re
from datetime import datetime, time, timedelta

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import CharField, Prefetch, Q, Value
from django.db.models.functions import Coalesce, NullIf
from django.utils.html import format_html
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.core.admin import OwnableAdminMixin, ProfileAdminMixin, SaveBeforeChangeAction
from apps.core.admin.mixins import _build_credentials_actions

from .exceptions import EvergoAPIError
from .forms import EvergoContractorLoginWizardForm, EvergoLoadCustomersForm, EvergoUserAdminForm
from .models import EvergoArtifact, EvergoCustomer, EvergoOrder, EvergoOrderFieldValue, EvergoUser


def _parse_selected_ids_query_param(request) -> list[int]:
    """Return validated integer IDs from a comma-separated ``id__in`` query parameter."""
    raw_ids = (request.GET.get("id__in") or request.GET.get("ids") or "").strip()
    if not raw_ids:
        return []

    selected_ids: list[int] = []
    for value in raw_ids.split(","):
        try:
            selected_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return selected_ids


def _initialize_contract_login_results(saved_contractor):
    """Return the default result payload shown after wizard validation attempts."""
    return {"admin_messages": [], "contractor": saved_contractor, "validated": None, "loaded": None}


def _message_level_label(level: int) -> str:
    """Return a stable status label for Django admin message levels."""
    if level >= messages.ERROR:
        return "error"
    if level >= messages.WARNING:
        return "warning"
    if level >= messages.SUCCESS:
        return "success"
    if level >= messages.INFO:
        return "info"
    return "debug"


def _message_user_with_feedback(admin_instance, request, setup_results, message, *, level: int):
    """Emit a Django admin message and mirror it into setup feedback data when available."""
    admin_instance.message_user(request, message, level=level)
    if setup_results is None:
        return
    setup_results["admin_messages"].append(
        {
            "message": message,
            "status": _message_level_label(level),
        }
    )


def _build_loaded_entities_links(summary: dict[str, list[int]]) -> str:
    """Build customer/order changelist links for the imported entities."""
    customer_ids = [str(value) for value in summary.get("loaded_customer_ids", [])]
    order_ids = [str(value) for value in summary.get("loaded_order_ids", [])]
    customers_url = reverse("admin:evergo_evergocustomer_changelist")
    orders_url = reverse("admin:evergo_evergoorder_changelist")
    if customer_ids:
        customers_url = f"{customers_url}?id__in={','.join(customer_ids)}"
    if order_ids:
        orders_url = f"{orders_url}?id__in={','.join(order_ids)}"
    return format_html(
        '{} <a href="{}">{}</a> | <a href="{}">{}</a>',
        _("View loaded items:"),
        customers_url,
        _("Customers"),
        orders_url,
        _("Orders"),
    )


def _run_contract_login_validation(admin_instance, request, form, contractor, *, show_setup_results: bool):
    """Validate Evergo credentials and optionally run the initial customer import."""
    setup_results = _initialize_contract_login_results(contractor) if show_setup_results else None
    if not form.cleaned_data.get("validate_credentials"):
        return setup_results

    login_result = contractor.test_login()
    success_message = _(
        "Evergo login succeeded for %(contractor)s (status %(status)s)."
    ) % {"contractor": str(contractor), "status": login_result.response_code}
    _message_user_with_feedback(
        admin_instance,
        request,
        setup_results,
        success_message,
        level=messages.SUCCESS,
    )
    if setup_results is not None:
        setup_results["validated"] = {"ok": True, "message": success_message}

    order_numbers = (form.cleaned_data.get("order_numbers") or "").strip()
    if not form.cleaned_data.get("load_all_customers") and not order_numbers:
        return setup_results

    try:
        summary = contractor.load_customers_from_queries(
            raw_queries="" if form.cleaned_data.get("load_all_customers") else order_numbers
        )
    except EvergoAPIError as exc:
        error_message = _("Customer load failed for %(contractor)s: %(error)s") % {
            "contractor": str(contractor),
            "error": exc,
        }
        _message_user_with_feedback(
            admin_instance,
            request,
            setup_results,
            error_message,
            level=messages.ERROR,
        )
        if setup_results is not None:
            setup_results["loaded"] = {"ok": False, "message": error_message}
        return setup_results

    load_label = _("Full load completed") if form.cleaned_data.get("load_all_customers") else _("Order load completed")
    load_message = _(
        "%(label)s. Customers: %(customers)s | "
        "Orders created: %(created)s | Orders updated: %(updated)s | "
        "Placeholders: %(placeholders)s"
    ) % {
        "label": load_label,
        "customers": summary["customers_loaded"],
        "created": summary["orders_created"],
        "updated": summary["orders_updated"],
        "placeholders": summary["placeholders_created"],
    }
    load_message_with_links = format_html("{} {}", load_message, _build_loaded_entities_links(summary))
    _message_user_with_feedback(
        admin_instance,
        request,
        setup_results,
        load_message_with_links,
        level=messages.SUCCESS,
    )
    if setup_results is not None:
        setup_results["loaded"] = {"ok": True, "message": load_message_with_links, "summary": summary}
    return setup_results


def _save_contractor_and_maybe_validate(admin_instance, request, form, profile):
    """Persist wizard changes and rollback validation failures for both create and update flows."""
    show_setup_results = (
        form.cleaned_data.get("validate_credentials")
        or form.cleaned_data.get("load_all_customers")
        or bool((form.cleaned_data.get("order_numbers") or "").strip())
    )

    try:
        with transaction.atomic():
            contractor = form.save()
            setup_results = _run_contract_login_validation(
                admin_instance,
                request,
                form,
                contractor,
                show_setup_results=show_setup_results,
            )
    except EvergoAPIError as exc:
        contractor = profile if profile is not None else form.save(commit=False)
        if profile is not None:
            contractor.refresh_from_db()
        else:
            contractor.pk = None
            contractor._state.adding = True
        setup_results = _initialize_contract_login_results(contractor) if show_setup_results else None
        if setup_results is not None:
            setup_results["validated"] = {"ok": False, "message": str(exc)}
        error_message = _("Evergo validation failed for %(contractor)s: %(error)s") % {
            "contractor": str(contractor),
            "error": exc,
        }
        _message_user_with_feedback(
            admin_instance,
            request,
            setup_results,
            error_message,
            level=messages.ERROR,
        )
        return contractor, setup_results

    return contractor, setup_results


def _handle_contract_login_wizard_post(admin_instance, request, profile, opts, changelist_url):
    """Bind, validate, and process contractor wizard submissions."""
    instance = profile if profile is not None else admin_instance.model()
    form = EvergoContractorLoginWizardForm(request.POST, instance=instance, request_user=request.user)
    setup_results = None
    if not form.is_valid():
        return form, setup_results

    contractor, setup_results = _save_contractor_and_maybe_validate(admin_instance, request, form, profile)
    if "_continue" in request.POST and not setup_results and contractor.pk:
        return redirect(reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[contractor.pk])), setup_results
    if "_save" in request.POST and not setup_results:
        return redirect(changelist_url), setup_results
    return form, setup_results


def _contractor_login_wizard_admin_view(admin_instance, request, object_id: str | None = None):
    """Render and process the Evergo contractor signup/login wizard."""
    opts = admin_instance.model._meta
    changelist_url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
    profile = None
    if object_id is not None:
        profile = get_object_or_404(admin_instance.get_queryset(request), pk=object_id)

    permission_granted = (
        admin_instance.has_change_permission(request, obj=profile)
        if profile is not None
        else admin_instance.has_add_permission(request)
    )
    if not permission_granted:
        raise PermissionDenied

    setup_results = None
    if request.method == "POST":
        form_or_response, setup_results = _handle_contract_login_wizard_post(
            admin_instance,
            request,
            profile,
            opts,
            changelist_url,
        )
        if hasattr(form_or_response, "status_code"):
            return form_or_response
        form = form_or_response
    else:
        instance = profile if profile is not None else admin_instance.model()
        form = EvergoContractorLoginWizardForm(instance=instance, request_user=request.user)

    context = {
        **admin_instance.admin_site.each_context(request),
        "opts": opts,
        "title": _("Login on Evergo"),
        "form": form,
        "original": profile,
        "profile": profile,
        "setup_results": setup_results,
        "changelist_url": changelist_url,
    }
    return render(request, "admin/evergo/contractor_login_wizard.html", context)


def _load_customers_admin_view(admin_instance, request):
    """Render and process the shared Evergo customer-loading wizard."""
    opts = admin_instance.model._meta
    changelist_url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
    orders_changelist_url = reverse("admin:evergo_evergoorder_changelist")

    if request.method == "POST":
        if not admin_instance.has_change_permission(request):
            raise PermissionDenied
    elif not admin_instance.has_view_or_change_permission(request):
        raise PermissionDenied

    if request.method == "POST":
        form = EvergoLoadCustomersForm(request.POST, request_user=request.user)
        if form.is_valid():
            profile = form.cleaned_data["profile"]
            raw_queries = form.cleaned_data["raw_queries"]
            load_mode = (request.POST.get("load_mode") or "filtered").strip().lower()
            if load_mode == "all":
                raw_queries = ""
            try:
                summary = profile.load_customers_from_queries(raw_queries=raw_queries)
            except EvergoAPIError as exc:
                admin_instance.message_user(
                    request,
                    _("Failed loading customers for %(profile)s: %(error)s")
                    % {"profile": str(profile), "error": exc},
                    level=messages.ERROR,
                )
            else:
                admin_instance.message_user(
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
                    admin_instance.message_user(
                        request,
                        _("Not found in Evergo: %(items)s")
                        % {"items": ", ".join(summary["unresolved"])},
                        level=messages.WARNING,
                    )
                next_view = form.cleaned_data.get("next_view") or "customers"
                selected_customer_ids = [str(value) for value in summary.get("loaded_customer_ids", [])]
                selected_order_ids = [str(value) for value in summary.get("loaded_order_ids", [])]

                if next_view == "customers":
                    destination_url = changelist_url
                    selected_ids = selected_customer_ids
                else:
                    destination_url = orders_changelist_url
                    selected_ids = selected_order_ids

                if selected_ids:
                    return HttpResponseRedirect(f"{destination_url}?id__in={','.join(selected_ids)}")
                return HttpResponseRedirect(destination_url)
    else:
        form = EvergoLoadCustomersForm(request_user=request.user)

    context = {
        **admin_instance.admin_site.each_context(request),
        "opts": admin_instance.model._meta,
        "title": _("Load Evergo Customers"),
        "form": form,
        "add_profile_url": reverse("admin:evergo_evergouser_add"),
    }
    return render(request, "admin/evergo/load_customers.html", context)


@admin.register(EvergoUser)
class EvergoUserAdmin(
    SaveBeforeChangeAction, OwnableAdminMixin, ProfileAdminMixin, DjangoObjectActions, admin.ModelAdmin
):
    """Manage Evergo users and allow login verification from admin actions."""

    change_form_template = "django_object_actions/change_form.html"
    form = EvergoUserAdminForm
    dashboard_actions = ("login_on_evergo_dashboard_action",)
    LOGIN_ON_EVERGO_LABEL = _("Login on Evergo")

    changelist_actions = ("my_profile",)

    list_display = (
        "evergo_email",
        "id",
        "owner_display",
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
    actions = ("_test_login_and_sync_bulk_action",)
    change_actions = ("login_on_evergo_action", "test_login_and_sync_action", "my_profile_action")
    fieldsets = (
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
        (
            "Ownership",
            {
                "fields": ("user", "group", "avatar"),
            },
        ),
    )

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        if not issubclass(form_class, EvergoUserAdminForm):
            return form_class

        class RequestUserEvergoUserAdminForm(form_class):
            def __init__(self, *args, **inner_kwargs):
                inner_kwargs.setdefault("request_user", request.user)
                super().__init__(*args, **inner_kwargs)

        return RequestUserEvergoUserAdminForm

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

    (
        _test_login_and_sync_bulk_action,
        test_login_and_sync_action,
    ) = _build_credentials_actions(
        "_test_login_and_sync_bulk_action",
        "_test_login_and_sync",
        _("Test and Sync"),
    )

    def get_dashboard_actions(self, request):
        """Return global dashboard shortcuts exposed for Evergo contractors."""
        return self.dashboard_actions

    def changelist_view(self, request, extra_context=None):
        """Inject a single Evergo wizard button into the changelist toolbar markup."""
        response = super().changelist_view(request, extra_context=extra_context)
        content = getattr(response, "rendered_content", "")
        label = str(self.LOGIN_ON_EVERGO_LABEL)
        if not content or label in content:
            return response

        link_markup = format_html(
            '<li><a href="{}" class="addlink">{}</a></li>',
            reverse("admin:evergo_evergouser_login_on_evergo"),
            label,
        )
        response.content = content.replace('<ul class="object-tools">', f'<ul class="object-tools">{link_markup}', 1)
        return response

    def get_urls(self):
        """Register Evergo contractor signup/login wizard routes."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "login-on-evergo/",
                self.admin_site.admin_view(self.login_on_evergo_view),
                name="evergo_evergouser_login_on_evergo",
            ),
            path(
                "<path:object_id>/login-on-evergo/",
                self.admin_site.admin_view(self.login_on_evergo_object_view),
                name="evergo_evergouser_login_on_evergo_object",
            ),
        ]
        return custom_urls + urls

    def login_on_evergo_wizard(self, request, queryset=None):
        """Open the contractor signup/login wizard from the changelist."""
        return redirect(reverse("admin:evergo_evergouser_login_on_evergo"))

    login_on_evergo_wizard.label = LOGIN_ON_EVERGO_LABEL
    login_on_evergo_wizard.short_description = LOGIN_ON_EVERGO_LABEL
    login_on_evergo_wizard.requires_queryset = False
    login_on_evergo_wizard.dashboard_url = "admin:evergo_evergouser_login_on_evergo"

    def login_on_evergo_dashboard_action(self, request, queryset=None):
        """Expose the contractor login wizard from the admin dashboard."""
        return redirect(reverse("admin:evergo_evergouser_login_on_evergo"))

    login_on_evergo_dashboard_action.label = LOGIN_ON_EVERGO_LABEL
    login_on_evergo_dashboard_action.short_description = LOGIN_ON_EVERGO_LABEL
    login_on_evergo_dashboard_action.requires_queryset = False
    login_on_evergo_dashboard_action.dashboard_url = "admin:evergo_evergouser_login_on_evergo"

    def login_on_evergo_action(self, request, obj):
        """Open the wizard prefilled with the selected contractor record."""
        return redirect(reverse("admin:evergo_evergouser_login_on_evergo_object", args=[obj.pk]))

    login_on_evergo_action.label = LOGIN_ON_EVERGO_LABEL
    login_on_evergo_action.short_description = LOGIN_ON_EVERGO_LABEL

    def login_on_evergo_view(self, request):
        """Render the contractor signup/login wizard without a preselected contractor."""
        return _contractor_login_wizard_admin_view(self, request)

    def login_on_evergo_object_view(self, request, object_id):
        """Render the contractor signup/login wizard for one existing contractor."""
        return _contractor_login_wizard_admin_view(self, request, object_id=object_id)



@admin.register(EvergoOrder)
class EvergoOrderAdmin(SaveBeforeChangeAction, DjangoObjectActions, admin.ModelAdmin):
    """Inspect synchronized Evergo order snapshots."""

    PROCESS_ORDER_LABEL = _("Process Order")

    change_form_template = "django_object_actions/change_form.html"
    changelist_actions = ("load_orders_wizard",)
    actions = ("reload_selected_from_evergo",)
    change_actions = ("process_so_action", "reload_from_evergo_action")

    list_display = (
        "order_number_link",
        "customer_name_link",
        "status_name_link",
        "address_display",
        "phone_display",
        "brand_name",
        "municipio_display",
    )
    list_display_links = ("order_number_link",)
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
    readonly_fields = (
        "evergo_flow_link",
        "customer_name_link",
        "validation_state_check",
        "phone_primary",
        "phone_secondary",
        "address_street",
        "address_num_ext",
        "address_num_int",
        "address_between_streets",
        "address_neighborhood",
        "address_municipality",
        "address_city",
        "address_state",
        "address_postal_code",
        "full_address",
        "source_age_days",
        "last_contact_age_days",
        "raw_payload",
        "refreshed_at",
        "created_at",
    )

    fieldsets = (
        (
            "Order",
            {
                "fields": (
                    "user",
                    "remote_id",
                    "order_number",
                    "customer_name_link",
                    "evergo_flow_link",
                    "status_name",
                    "site_name",
                    "validation_state_check",
                )
            },
        ),
        (
            "Contact",
            {
                "fields": ("phone_primary", "phone_secondary"),
            },
        ),
        (
            "Address",
            {
                "fields": (
                    "address_street",
                    "address_num_ext",
                    "address_num_int",
                    "address_between_streets",
                    "address_neighborhood",
                    "address_municipality",
                    "address_city",
                    "address_state",
                    "address_postal_code",
                    "full_address",
                )
            },
        ),
        (
            "Aging",
            {"fields": ("source_created_at", "source_age_days", "source_last_contact_at", "last_contact_age_days")},
        ),
        (
            "Timestamps",
            {"fields": ("source_updated_at", "refreshed_at", "created_at")},
        ),
        (
            "Raw payload",
            {"fields": ("raw_payload",)},
        ),
    )

    @admin.display(description="Brand", ordering="site_name")
    def brand_name(self, obj):
        """Display the synced site name as brand column in list view."""
        return obj.site_name

    @admin.display(description="Address", ordering="address_street")
    def address_display(self, obj):
        """Display the primary street address in changelist rows."""
        return obj.address_street or "-"

    @admin.display(description="Municipio", ordering="address_municipality")
    def municipio_display(self, obj):
        """Display municipality value in changelist rows."""
        return obj.address_municipality or "-"

    @admin.display(description="Phone")
    def phone_display(self, obj):
        """Display primary phone, falling back to secondary when needed."""
        return obj.phone_primary or obj.phone_secondary or "-"

    @admin.display(description="Order Number", ordering="order_number")
    def order_number_link(self, obj):
        """Render order number as the primary record link in changelist rows."""
        value = obj.order_number or str(obj.remote_id)
        return value

    @admin.display(description="Status Name", ordering="status_name")
    def status_name_link(self, obj):
        """Render status text linking directly to the public Evergo process flow."""
        if not getattr(obj, "remote_id", None):
            return "-"
        value = obj.status_name or "-"
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>', self._flow_url(obj), value)

    @admin.display(description="Customer")
    def customer_name_link(self, obj):
        """Show the linked customer as a direct link to its admin change view."""
        customer = obj.customers.first()
        if customer is None:
            return "-"
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:evergo_evergocustomer_change", args=[customer.pk]),
            customer.name,
        )

    @admin.display(description="Assigned Engineer", ordering="assigned_engineer_name")
    def assigned_engineer_name_cleaved(self, obj):
        """Return engineer name with bracketed fragments removed (cleaved)."""
        raw_name = (obj.assigned_engineer_name or "").strip()
        cleaved_name = re.sub(r"\[[^\]]*\]", "", raw_name)
        normalized_name = " ".join(cleaved_name.split())
        return normalized_name or "-"

    @admin.display(description=PROCESS_ORDER_LABEL)
    def evergo_flow_link(self, obj):
        """Show a direct link to the Evergo order processing flow on change view."""
        if not getattr(obj, "remote_id", None):
            return "-"
        return format_html(
            '<a class="button" href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            self._flow_url(obj),
            self.PROCESS_ORDER_LABEL,
        )

    def _flow_url(self, obj):
        """Build the public order tracking flow URL for an Evergo order snapshot."""
        if not getattr(obj, "remote_id", None):
            return ""
        return reverse("evergo:order-tracking-public", kwargs={"order_id": obj.remote_id})

    def get_queryset(self, request):
        """Restrict order visibility to the current user's linked Evergo profile."""
        queryset = super().get_queryset(request).prefetch_related(
            Prefetch("customers", queryset=EvergoCustomer.objects.order_by("pk"))
        )
        selected_ids = _parse_selected_ids_query_param(request)
        if selected_ids:
            queryset = queryset.filter(pk__in=selected_ids)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(user__user=request.user)

    def has_add_permission(self, request):
        """Disallow manual admin creation; orders are synchronized from Evergo."""
        return False

    @admin.display(description="Valid", boolean=True)
    def validation_state_check(self, obj):
        """Render a checkmark when the order is fully validated upstream."""
        return obj.validation_state == EvergoOrder.VALIDATION_STATE_VALIDATED

    @admin.display(description="Full address")
    def full_address(self, obj):
        """Concatenate address parts for easy copy/paste."""
        parts = [
            obj.address_street,
            obj.address_num_ext,
            obj.address_num_int,
            obj.address_between_streets,
            obj.address_neighborhood,
            obj.address_municipality,
            obj.address_city,
            obj.address_state,
            obj.address_postal_code,
        ]
        return " ".join(part for part in parts if part).strip()

    @admin.display(description="Days since source created")
    def source_age_days(self, obj):
        """Return elapsed days since the source order creation timestamp."""
        if not obj.source_created_at:
            return "-"
        return (timezone.now() - obj.source_created_at).days

    @admin.display(description="Days since last contact/comment")
    def last_contact_age_days(self, obj):
        """Return elapsed days since last known contact/comment timestamp."""
        if not obj.source_last_contact_at:
            return "-"
        return (timezone.now() - obj.source_last_contact_at).days

    def load_orders_wizard(self, request, queryset=None):
        """Redirect the order dashboard tool to the shared load wizard view."""
        return HttpResponseRedirect(reverse("admin:evergo_evergocustomer_load_customers"))

    load_orders_wizard.label = _("Load Orders")
    load_orders_wizard.short_description = _("Load Orders")
    load_orders_wizard.requires_queryset = False

    def process_so_action(self, request, obj):
        """Expose a change-view tool button that opens the SO processing flow."""
        if not getattr(obj, "remote_id", None):
            self.message_user(request, _("Order has no remote ID yet."), level=messages.WARNING)
            return HttpResponseRedirect(reverse("admin:evergo_evergoorder_change", args=[obj.pk]))
        return HttpResponseRedirect(self._flow_url(obj))

    process_so_action.label = PROCESS_ORDER_LABEL
    process_so_action.short_description = PROCESS_ORDER_LABEL

    def _reload_order_from_evergo(self, request, order):
        """Delete stale order data and rehydrate the order directly from Evergo."""
        try:
            refreshed_order = order.user.reload_order_from_remote(order=order)
        except EvergoAPIError as exc:
            self.message_user(
                request,
                _("Failed to reload order %(order)s from Evergo: %(error)s")
                % {"order": str(order), "error": exc},
                level=messages.ERROR,
            )
            return None

        self.message_user(
            request,
            _("Reloaded order %(order)s from Evergo.") % {"order": str(order)},
            level=messages.SUCCESS,
        )
        return refreshed_order

    def reload_selected_from_evergo(self, request, queryset):
        """Admin bulk action that refreshes selected orders from Evergo API payloads."""
        reloaded = 0
        for order in queryset:
            if self._reload_order_from_evergo(request, order):
                reloaded += 1

        if reloaded:
            self.message_user(
                request,
                _("Evergo reload finished. Orders refreshed: %(count)s") % {"count": reloaded},
                level=messages.SUCCESS,
            )

    reload_selected_from_evergo.short_description = _("Reload selected from Evergo")

    def reload_from_evergo_action(self, request, obj):
        """Change-view action to refresh one order snapshot from Evergo."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        refreshed_order = self._reload_order_from_evergo(request, obj)
        target_pk = refreshed_order.pk if refreshed_order is not None else obj.pk
        return HttpResponseRedirect(reverse("admin:evergo_evergoorder_change", args=[target_pk]))

    reload_from_evergo_action.label = _("Reload from Evergo")
    reload_from_evergo_action.short_description = _("Reload from Evergo")
    reload_from_evergo_action.methods = ("POST",)
    reload_from_evergo_action.button_type = "form"


@admin.register(EvergoOrderFieldValue)
class EvergoOrderFieldValueAdmin(admin.ModelAdmin):
    """Inspect learned dropdown catalog values from Evergo."""

    list_display = ("field_name", "remote_id", "remote_name", "local_label", "last_seen_at")
    list_filter = ("field_name",)
    search_fields = ("field_name", "remote_name", "local_label")
    readonly_fields = ("last_seen_at", "created_at", "raw_payload")


class EvergoArtifactInline(admin.TabularInline):
    """Allow image/PDF attachments directly from customer admin."""

    model = EvergoArtifact
    extra = 1
    fields = ("file", "artifact_type", "created_at")
    readonly_fields = ("artifact_type", "created_at")


@admin.register(EvergoArtifact)
class EvergoArtifactAdmin(admin.ModelAdmin):
    """Manage artifacts linked to Evergo customers."""

    list_display = ("customer", "artifact_type", "file", "created_at")
    list_filter = ("artifact_type", "customer__user")
    search_fields = ("customer__name", "customer__latest_so", "file")
    readonly_fields = ("artifact_type", "created_at")


class _CustomerDateRangeFilter(admin.SimpleListFilter):
    """Shared date-range filter helper used by Evergo customer changelist filters."""

    parameter_name = ""
    title = ""
    field_name = ""

    def lookups(self, request, model_admin):
        """Expose common date-range options for customer list filtering."""
        return (
            ("today", _("Today")),
            ("week", _("This week")),
            ("month", _("This month")),
        )

    def _bounds(self) -> tuple[datetime, datetime] | None:
        """Build timezone-aware datetime bounds for the selected date range."""
        value = self.value()
        if value not in {"today", "week", "month"}:
            return None

        today = timezone.localdate()
        start_date = today
        if value == "week":
            start_date = today - timedelta(days=today.weekday())
        elif value == "month":
            start_date = today.replace(day=1)

        start = timezone.make_aware(datetime.combine(start_date, time.min))
        end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
        return start, end

    def queryset(self, request, queryset):
        """Apply a date range to the configured model field when selected."""
        bounds = self._bounds()
        if not bounds or not self.field_name:
            return queryset
        start, end = bounds
        return queryset.filter(**{f"{self.field_name}__gte": start, f"{self.field_name}__lt": end})


class CustomerLoadedAtFilter(_CustomerDateRangeFilter):
    """Filter Evergo customers by local load timestamp ranges."""

    title = _("Loaded locally")
    parameter_name = "loaded_at_range"
    field_name = "created_at"


class CustomerUpdatedAtFilter(_CustomerDateRangeFilter):
    """Filter Evergo customers by local update timestamp ranges."""

    title = _("Updated locally")
    parameter_name = "updated_at_range"
    field_name = "refreshed_at"


class CustomerRemoteUpdatedAtFilter(_CustomerDateRangeFilter):
    """Filter Evergo customers by last remote order update timestamp ranges."""

    title = _("Updated remotely")
    parameter_name = "remote_updated_at_range"
    field_name = "latest_order_updated_at"


class CustomerCityFilter(admin.SimpleListFilter):
    """Filter customers by city-like locality from municipio/ciudad payload values."""

    title = _("City / Municipio")
    parameter_name = "city_municipio"

    def lookups(self, request, model_admin):
        """Return distinct municipality/city values from customer payloads."""
        values = set()
        queryset = model_admin.get_queryset(request)
        for payload in queryset.values_list("raw_payload", flat=True):
            if not isinstance(payload, dict):
                continue
            install_payload = payload.get("orden_instalacion")
            if not isinstance(install_payload, dict):
                continue
            municipio = str(install_payload.get("municipio") or "").strip()
            ciudad = str(install_payload.get("ciudad") or "").strip()
            if municipio:
                values.add(municipio)
            elif ciudad:
                values.add(ciudad)
        return [(value, value) for value in sorted(values)]

    def queryset(self, request, queryset):
        """Filter customers where municipio or ciudad exactly matches selected value."""
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(
            Q(raw_payload__orden_instalacion__municipio__iexact=value)
            | Q(raw_payload__orden_instalacion__ciudad__iexact=value)
        )


class CustomerStatusFilter(admin.SimpleListFilter):
    """Filter customers by latest order status label."""

    title = _("Status")
    parameter_name = "last_so_status"

    def lookups(self, request, model_admin):
        """Return distinct status names from linked latest orders."""
        statuses = (
            model_admin.get_queryset(request)
            .exclude(latest_order__status_name="")
            .exclude(latest_order__status_name__isnull=True)
            .values_list("latest_order__status_name", flat=True)
            .distinct()
            .order_by("latest_order__status_name")
        )
        return [(status, status) for status in statuses]

    def queryset(self, request, queryset):
        """Filter customers where latest order status equals selected value."""
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(latest_order__status_name=value)


@admin.register(EvergoCustomer)
class EvergoCustomerAdmin(DjangoObjectActions, admin.ModelAdmin):
    """Inspect customer snapshots synchronized from Evergo orders."""

    changelist_actions = ("load_customers_wizard",)
    actions = ("reload_selected_from_evergo",)
    list_select_related = ("latest_order",)

    list_display = (
        "latest_so_link",
        "name",
        "address_display",
        "brand_display",
        "phone_number_display",
    )
    list_filter = (
        CustomerCityFilter,
        CustomerStatusFilter,
        CustomerLoadedAtFilter,
        CustomerUpdatedAtFilter,
        CustomerRemoteUpdatedAtFilter,
    )
    list_display_links = ("name",)
    search_fields = ("latest_so", "name", "phone_number", "address", "email")
    readonly_fields = ("status_of_last_so", "phone_number_display", "raw_payload", "refreshed_at", "created_at")
    inlines = (EvergoArtifactInline,)
    view_on_site = True

    @staticmethod
    def _get_latest_order(obj: EvergoCustomer) -> EvergoOrder | None:
        """Return linked latest order and tolerate stale/deleted FK references."""
        try:
            return obj.latest_order
        except EvergoOrder.DoesNotExist:
            return None

    def get_queryset(self, request):
        """Limit customer rows to the signed-in owner unless user is superuser."""
        queryset = super().get_queryset(request).annotate(
            brand_sort_value=Coalesce(
                NullIf("latest_order__site_name", Value("")),
                "raw_payload__orden_instalacion__marca_cargador__text",
                output_field=CharField(),
            )
        )
        selected_ids = _parse_selected_ids_query_param(request)
        if selected_ids:
            queryset = queryset.filter(pk__in=selected_ids)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(user__user=request.user)

    def get_urls(self):
        """Register custom admin routes for Evergo customer tools."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "load-customers/",
                self.admin_site.admin_view(self.load_customers_view),
                name="evergo_evergocustomer_load_customers",
            )
        ]
        return custom_urls + urls

    def load_customers_wizard(self, request, queryset=None):
        """Redirect changelist action to the customer-loading wizard."""
        return HttpResponseRedirect(reverse("admin:evergo_evergocustomer_load_customers"))

    load_customers_wizard.label = _("Load Customers")
    load_customers_wizard.short_description = _("Load Customers")
    load_customers_wizard.requires_queryset = False

    @admin.display(description=_("Status of Last SO"))
    def status_of_last_so(self, obj):
        """Return the latest order status label for the customer."""
        latest_order = self._get_latest_order(obj)
        if not (latest_order and latest_order.status_name):
            return "-"
        if not latest_order.remote_id:
            return latest_order.status_name
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            reverse("evergo:order-tracking-public", kwargs={"order_id": latest_order.remote_id}),
            latest_order.status_name,
        )

    @admin.display(description=_("Brand"), ordering="brand_sort_value")
    def brand_display(self, obj):
        """Return charger/site brand inferred from the linked latest order payload."""
        latest_order = self._get_latest_order(obj)
        if latest_order and latest_order.site_name:
            return latest_order.site_name

        install_payload = obj.raw_payload.get("orden_instalacion") if isinstance(obj.raw_payload, dict) else {}
        if not isinstance(install_payload, dict):
            return "-"
        return str(install_payload.get("marca_cargador") or "").strip() or "-"

    @admin.display(description=_("Phone number"))
    def phone_number_display(self, obj):
        """Return a cleaned phone value without Mexico +52/52 dialing prefixes."""
        phone = (obj.phone_number or "").strip()
        compact = phone.replace(" ", "").replace("-", "")
        if compact.startswith(("+52", "52")):
            return re.sub(r"^\+?\s*52[\s-]*", "", phone)
        return phone

    @admin.display(description=_("Last SO"), ordering="latest_so")
    def latest_so_link(self, obj):
        """Render latest SO value and link it to the linked latest order when available."""
        if not obj.latest_so:
            return "-"
        if not obj.latest_order_id:
            return obj.latest_so
        change_url = reverse("admin:evergo_evergoorder_change", args=[obj.latest_order_id])
        return format_html('<a href="{}">{}</a>', change_url, obj.latest_so)

    @admin.display(description=_("Address"), ordering="address")
    def address_display(self, obj):
        """Return address with normalized municipio/ciudad duplication removed."""
        address = (obj.address or "").strip()
        install_payload = obj.raw_payload.get("orden_instalacion") if isinstance(obj.raw_payload, dict) else {}
        if not isinstance(install_payload, dict):
            return address
        municipio = str(install_payload.get("municipio") or "").strip()
        ciudad = str(install_payload.get("ciudad") or "").strip()
        if not municipio or not ciudad:
            return address
        if ciudad.lower() == municipio.lower() or municipio.lower() in ciudad.lower():
            return re.sub(re.escape(ciudad), municipio, address, flags=re.IGNORECASE)
        return address

    def load_customers_view(self, request):
        """Render/handle the sales-order customer import wizard."""
        return _load_customers_admin_view(self, request)

    def reload_selected_from_evergo(self, request, queryset):
        """Delete selected customer cache rows and refetch each one from Evergo."""
        refreshed = 0
        for customer in queryset.select_related("user", "latest_order"):
            customer_label = str(customer)
            try:
                customer.user.reload_customer_from_remote(customer=customer)
            except EvergoAPIError as exc:
                self.message_user(
                    request,
                    _("Failed to reload customer %(customer)s from Evergo: %(error)s")
                    % {"customer": customer_label, "error": exc},
                    level=messages.ERROR,
                )
                continue

            refreshed += 1
            self.message_user(
                request,
                _("Reloaded customer %(customer)s from Evergo.") % {"customer": customer_label},
                level=messages.SUCCESS,
            )

        if refreshed:
            self.message_user(
                request,
                _("Evergo reload finished. Customers refreshed: %(count)s") % {"count": refreshed},
                level=messages.SUCCESS,
            )

    reload_selected_from_evergo.short_description = _("Reload selected from Evergo")
