from __future__ import annotations

import logging
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _, ngettext

from apps.cards.models import RFID
from apps.groups.models import SecurityGroup
from apps.locals.user_data import EntityModelAdmin
from apps.odoo.models import OdooEmployee
from apps.vehicle.models import ElectricVehicle

from ..models import CustomerAccount, EnergyTransaction
from .forms import CustomerAccountRFIDForm, OdooCustomerSearchForm


logger = logging.getLogger(__name__)


class CustomerAccountRFIDInline(admin.TabularInline):
    model = CustomerAccount.rfids.through
    form = CustomerAccountRFIDForm
    autocomplete_fields = ["rfid"]
    extra = 0
    verbose_name = "RFID"
    verbose_name_plural = "RFIDs"


class EnergyTransactionInline(admin.TabularInline):
    model = EnergyTransaction
    fields = (
        "direction",
        "delta_kw",
        "tariff",
        "charged_amount_mxn",
        "conversion_factor",
        "source",
        "reference",
        "created_on",
    )
    readonly_fields = ("created_on",)
    extra = 0
    autocomplete_fields = ["tariff"]


@admin.register(CustomerAccount)
class CustomerAccountAdmin(EntityModelAdmin):
    change_list_template = "admin/core/customeraccount/change_list.html"
    change_form_template = "admin/user_datum_change_form.html"
    list_display = (
        "name",
        "user",
        "credits_kw",
        "total_kw_spent",
        "balance_kw",
        "balance_mxn",
        "service_account",
        "authorized",
    )
    search_fields = (
        "name",
        "user__username",
        "user__email",
        "user__first_name",
        "user__last_name",
    )
    readonly_fields = (
        "credits_kw",
        "total_kw_spent",
        "balance_kw",
        "authorized",
    )
    inlines = [CustomerAccountRFIDInline, EnergyTransactionInline]
    actions = ["test_authorization"]
    fieldsets = (
        (None, {"fields": ("name", "user", ("service_account", "authorized"))}),
        (
            "Live Subscription",
            {
                "fields": (
                    "live_subscription_product",
                    ("live_subscription_start_date", "live_subscription_next_renewal"),
                )
            },
        ),
        (
            "Billing",
            {
                "fields": (
                    "balance_mxn",
                    "minimum_purchase_mxn",
                    "energy_tariff",
                    "credit_card_brand",
                    ("credit_card_last4", "credit_card_exp_month", "credit_card_exp_year"),
                )
            },
        ),
        (
            "Odoo",
            {
                "fields": ("odoo_customer",),
                "classes": ("collapse",),
            },
        ),
        (
            "Energy Summary",
            {
                "fields": (
                    "credits_kw",
                    "total_kw_spent",
                    "balance_kw",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def authorized(self, obj):
        return obj.can_authorize()

    authorized.boolean = True
    authorized.short_description = "Authorized"

    def test_authorization(self, request, queryset):
        for acc in queryset:
            if acc.can_authorize():
                self.message_user(request, f"{acc.user} authorized")
            else:
                self.message_user(request, f"{acc.user} denied")

    test_authorization.short_description = "Test authorization"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "onboard/",
                self.admin_site.admin_view(self.onboard_details),
                name="core_customeraccount_onboard_details",
            ),
            path(
                "import-from-odoo/",
                self.admin_site.admin_view(self.import_from_odoo_view),
                name="core_customeraccount_import_from_odoo",
            ),
        ]
        return custom + urls

    def onboard_details(self, request):
        class OnboardForm(forms.Form):
            first_name = forms.CharField(label="First name")
            last_name = forms.CharField(label="Last name")
            rfid = forms.CharField(required=False, label="RFID")
            allow_login = forms.BooleanField(
                required=False, initial=False, label="Allow login"
            )
            vehicle_id = forms.CharField(required=False, label="Electric Vehicle ID")

        if request.method == "POST":
            form = OnboardForm(request.POST)
            if form.is_valid():
                User = get_user_model()
                first = form.cleaned_data["first_name"]
                last = form.cleaned_data["last_name"]
                allow = form.cleaned_data["allow_login"]
                username = f"{first}.{last}".lower()
                user = User.objects.create_user(
                    username=username,
                    first_name=first,
                    last_name=last,
                    is_active=allow,
                )
                account = CustomerAccount.objects.create(user=user, name=username.upper())
                rfid_val = form.cleaned_data["rfid"].upper()
                if rfid_val:
                    tag, _ = RFID.register_scan(rfid_val)
                    account.rfids.add(tag)
                vehicle_vin = form.cleaned_data["vehicle_id"]
                if vehicle_vin:
                    ElectricVehicle.objects.create(account=account, vin=vehicle_vin)
                self.message_user(request, "Customer onboarded")
                return redirect("admin:core_customeraccount_changelist")
        else:
            form = OnboardForm()

        context = self.admin_site.each_context(request)
        context.update({"form": form})
        return render(request, "core/onboard_details.html", context)

    def _odoo_employee_admin(self):
        return self.admin_site._registry.get(OdooEmployee)

    @staticmethod
    def _simplify_customer(customer: dict[str, Any]) -> dict[str, Any]:
        country = ""
        country_info = customer.get("country_id")
        if isinstance(country_info, (list, tuple)) and len(country_info) > 1:
            country = country_info[1]
        return {
            "id": customer.get("id"),
            "name": customer.get("name", ""),
            "email": customer.get("email", ""),
            "phone": customer.get("phone", ""),
            "mobile": customer.get("mobile", ""),
            "city": customer.get("city", ""),
            "country": country,
        }

    @staticmethod
    def _customer_fields() -> list[str]:
        return ["name", "email", "phone", "mobile", "city", "country_id"]

    def _build_customer_domain(self, cleaned_data: dict[str, Any]) -> list[list[str]]:
        domain: list[list[str]] = [["customer_rank", ">", 0]]
        if cleaned_data.get("name"):
            domain.append(["name", "ilike", cleaned_data["name"]])
        if cleaned_data.get("email"):
            domain.append(["email", "ilike", cleaned_data["email"]])
        if cleaned_data.get("phone"):
            domain.append(["phone", "ilike", cleaned_data["phone"]])
        return domain

    @staticmethod
    def _build_unique_account_name(base: str) -> str:
        base_name = (base or "").strip().upper() or "ODOO CUSTOMER"
        candidate = base_name
        suffix = 1
        while CustomerAccount.objects.filter(name=candidate).exists():
            suffix += 1
            candidate = f"{base_name}-{suffix}"
        return candidate

    @staticmethod
    def _odoo_security_group() -> SecurityGroup:
        group, _ = SecurityGroup.objects.get_or_create(name="Odoo User")
        return group

    def _ensure_odoo_user_group(self, user):
        group = self._odoo_security_group()
        if not user.groups.filter(pk=group.pk).exists():
            user.groups.add(group)

    def _record_odoo_error(
        self,
        request,
        context: dict[str, Any],
        exc: Exception,
        profile: OdooEmployee,
    ) -> None:
        logger.exception(
            "Failed to fetch Odoo customers for user %s (profile_id=%s, host=%s, database=%s)",
            getattr(getattr(request, "user", None), "pk", None),
            getattr(profile, "pk", None),
            getattr(profile, "host", None),
            getattr(profile, "database", None),
        )
        context["error"] = _("Unable to fetch customers from Odoo.")
        if getattr(request.user, "is_superuser", False):
            fault = getattr(exc, "faultString", "")
            message = str(exc)
            details = [
                f"Host: {getattr(profile, 'host', '')}",
                f"Database: {getattr(profile, 'database', '')}",
                f"User ID: {getattr(profile, 'odoo_uid', '')}",
            ]
            if fault and fault != message:
                details.append(f"Fault: {fault}")
            if message:
                details.append(f"Exception: {type(exc).__name__}: {message}")
            else:
                details.append(f"Exception type: {type(exc).__name__}")
            context["debug_error"] = "\n".join(details)

    def _fetch_odoo_customers(
        self, profile: OdooEmployee, cleaned_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        limit = cleaned_data.get("limit") or 50
        customers = profile.execute(
            "res.partner",
            "search_read",
            self._build_customer_domain(cleaned_data),
            fields=self._customer_fields(),
            limit=limit,
        )
        return [self._simplify_customer(customer) for customer in customers]

    def _fetch_customers_by_id(
        self, profile: OdooEmployee, identifiers: list[int]
    ) -> list[dict[str, Any]]:
        if not identifiers:
            return []
        customers = profile.execute(
            "res.partner",
            "search_read",
            [["id", "in", identifiers]],
            fields=self._customer_fields(),
        )
        return [self._simplify_customer(customer) for customer in customers]

    def _ensure_user_for_customer(
        self, customer: dict[str, Any] | None
    ) -> get_user_model() | None:
        if not customer:
            return None
        name = customer.get("name") or "customer"
        username = slugify(name).replace("-", "") or "customer"
        existing = get_user_model().objects.filter(username=username).first()
        if existing:
            return existing
        return get_user_model().objects.create_user(
            username=username,
            first_name=customer.get("name", ""),
            email=customer.get("email", ""),
            is_active=False,
        )

    def _import_selected_customers(
        self,
        request,
        profile: OdooEmployee,
        customers: list[dict[str, Any]],
        action: str,
        context: dict[str, Any],
    ) -> HttpResponseRedirect | None:
        identifiers = request.POST.getlist("customer_ids")
        if not identifiers:
            context["form_error"] = "Select customers before importing."
            return None
        results = profile.execute(
            "res.partner",
            "read",
            [int(identifier) for identifier in identifiers],
            fields=self._customer_fields(),
        )
        created = 0
        skipped = 0
        for customer in results:
            identifier = customer.get("id")
            account_name = self._build_unique_account_name(customer.get("name", ""))
            if CustomerAccount.objects.filter(odoo_customer__id=identifier).exists():
                skipped += 1
                continue
            if CustomerAccount.objects.filter(name=account_name).exists():
                skipped += 1
                continue
            user = None
            if customer.get("email"):
                user = self._ensure_user_for_customer(customer)
                if user is None:
                    skipped += 1
                    continue
            user = self._ensure_user_for_customer(customer)
            odoo_customer = {
                "id": identifier,
                "name": customer.get("name", ""),
                "email": customer.get("email", ""),
                "phone": customer.get("phone", ""),
                "mobile": customer.get("mobile", ""),
                "city": customer.get("city", ""),
                "country": customer.get("country", ""),
            }
            if user:
                existing_for_user = self.model.objects.filter(user=user).first()
                if existing_for_user:
                    self._ensure_odoo_user_group(user)
                    if existing_for_user.odoo_customer != odoo_customer:
                        existing_for_user.odoo_customer = odoo_customer
                        existing_for_user.save(update_fields=["odoo_customer"])
                    skipped += 1
                    continue

            account = self.model.objects.create(
                name=account_name,
                user=user,
                odoo_customer=odoo_customer,
            )
            self.log_addition(request, account, "Imported customer from Odoo")
            created += 1

        if created:
            self.message_user(
                request,
                ngettext(
                    "Imported %(count)d customer account from Odoo.",
                    "Imported %(count)d customer accounts from Odoo.",
                    created,
                )
                % {"count": created},
                level=messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d customer already imported.",
                    "Skipped %(count)d customers already imported.",
                    skipped,
                )
                % {"count": skipped},
                level=messages.WARNING,
            )

        if action == "import":
            return HttpResponseRedirect(reverse("admin:core_customeraccount_changelist"))
        return None

    def import_from_odoo_view(self, request):
        opts = self.model._meta
        search_form = OdooCustomerSearchForm(request.POST or None)
        context = self.admin_site.each_context(request)
        context.update(
            {
                "opts": opts,
                "title": _("Import from Odoo"),
                "has_credentials": False,
                "profile_url": None,
                "customers": [],
                "credential_error": None,
                "error": None,
                "debug_error": None,
                "form_error": None,
                "searched": False,
                "selected_ids": request.POST.getlist("customer_ids"),
                "search_form": search_form,
            }
        )

        profile_admin = self._odoo_employee_admin()
        if profile_admin is not None:
            context["profile_url"] = profile_admin.get_my_profile_url(request)

        profile = getattr(request.user, "odoo_employee", None)
        if not profile or not profile.is_verified:
            context["credential_error"] = _(
                "Configure your Odoo employee before importing customers."
            )
            return TemplateResponse(
                request, "admin/core/customeraccount/import_from_odoo.html", context
            )

        context["has_credentials"] = True
        customers: list[dict[str, Any]] = []
        action = request.POST.get("import_action")

        if request.method == "POST" and search_form.is_valid():
            context["searched"] = True
            try:
                customers = self._fetch_odoo_customers(profile, search_form.cleaned_data)
            except Exception as exc:
                self._record_odoo_error(request, context, exc, profile)
            else:
                context["customers"] = customers

            if action in ("import", "continue") and not context.get("error"):
                response = self._import_selected_customers(
                    request, profile, customers, action, context
                )
                if response is not None:
                    return response

        return TemplateResponse(
            request, "admin/core/customeraccount/import_from_odoo.html", context
        )


@admin.register(EnergyTransaction)
class EnergyTransactionAdmin(EntityModelAdmin):
    list_display = (
        "account",
        "direction",
        "delta_kw",
        "charged_amount_mxn",
        "source",
        "created_on",
    )
    list_filter = ("direction", "source", "created_on")
    search_fields = ("account__name", "account__user__username", "reference")
    readonly_fields = ("created_on",)
    autocomplete_fields = ["account", "tariff"]
