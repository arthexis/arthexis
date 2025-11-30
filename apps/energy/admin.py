from __future__ import annotations

import json
import logging
import re
from typing import Any

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _, ngettext

from apps.core.models import RFID, SecurityGroup
from apps.crms.models import OdooProfile
from apps.locals.user_data import EntityModelAdmin
from apps.ocpp.models import Charger, ElectricVehicle

from .models import (
    ClientReport,
    ClientReportSchedule,
    CustomerAccount,
    EnergyCredit,
    EnergyTariff,
    EnergyTransaction,
    Location,
)


logger = logging.getLogger(__name__)


class CustomerAccountRFIDForm(forms.ModelForm):
    """Form for assigning existing RFIDs to a customer account."""

    class Meta:
        model = CustomerAccount.rfids.through
        fields = ["rfid"]

    def clean_rfid(self):
        rfid = self.cleaned_data["rfid"]
        if rfid.energy_accounts.exclude(pk=self.instance.customeraccount_id).exists():
            raise forms.ValidationError(
                "RFID is already assigned to another customer account"
            )
        return rfid


class CustomerAccountRFIDInline(admin.TabularInline):
    model = CustomerAccount.rfids.through
    form = CustomerAccountRFIDForm
    autocomplete_fields = ["rfid"]
    extra = 0
    verbose_name = "RFID"
    verbose_name_plural = "RFIDs"


class EnergyCreditInline(admin.TabularInline):
    model = EnergyCredit
    fields = ("amount_kw", "created_by", "created_on")
    readonly_fields = ("created_by", "created_on")
    extra = 0


class EnergyTransactionInline(admin.TabularInline):
    model = EnergyTransaction
    fields = (
        "tariff",
        "purchased_kw",
        "charged_amount_mxn",
        "conversion_factor",
        "created_on",
    )
    readonly_fields = ("created_on",)
    extra = 0
    autocomplete_fields = ["tariff"]


class OdooCustomerSearchForm(forms.Form):
    name = forms.CharField(required=False, label=_("Name contains"))
    email = forms.CharField(required=False, label=_("Email contains"))
    phone = forms.CharField(required=False, label=_("Phone contains"))
    limit = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=200,
        initial=50,
        label=_("Result limit"),
        help_text=_("Limit the number of Odoo customers returned per search."),
    )


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
    inlines = [CustomerAccountRFIDInline, EnergyCreditInline, EnergyTransactionInline]
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
            "CRM",
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

    def save_formset(self, request, form, formset, change):
        objs = formset.save(commit=False)
        for obj in objs:
            if isinstance(obj, EnergyCredit) and not obj.created_by:
                obj.created_by = request.user
            obj.save()
        formset.save_m2m()

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

    def _odoo_profile_admin(self):
        return self.admin_site._registry.get(OdooProfile)

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
        profile: OdooProfile,
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
        self, profile: OdooProfile, cleaned_data: dict[str, Any]
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
        self, profile: OdooProfile, identifiers: list[int]
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
        profile: OdooProfile,
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

        profile_admin = self._odoo_profile_admin()
        if profile_admin is not None:
            context["profile_url"] = profile_admin.get_my_profile_url(request)

        profile = getattr(request.user, "odoo_profile", None)
        if not profile or not profile.is_verified:
            context["credential_error"] = _(
                "Configure your CRM employee credentials before importing customers."
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


@admin.register(EnergyCredit)
class EnergyCreditAdmin(EntityModelAdmin):
    list_display = ("account", "amount_kw", "created_by", "created_on")
    readonly_fields = ("created_by", "created_on")

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(EnergyTransaction)
class EnergyTransactionAdmin(EntityModelAdmin):
    list_display = (
        "account",
        "tariff",
        "purchased_kw",
        "charged_amount_mxn",
        "conversion_factor",
        "created_on",
    )
    readonly_fields = ("created_on",)
    autocomplete_fields = ["account", "tariff"]


class LocationAdminForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = "__all__"
        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "any"}),
            "longitude": forms.NumberInput(attrs={"step": "any"}),
        }

    class Media:
        css = {"all": ("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",)}
        js = (
            "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
            "ocpp/charger_map.js",
        )


@admin.register(Location)
class LocationAdmin(EntityModelAdmin):
    form = LocationAdminForm
    list_display = (
        "name",
        "zone",
        "contract_type",
        "city",
        "state",
        "assigned_to",
    )
    list_filter = ("zone", "contract_type", "city", "state", "country")
    search_fields = ("name", "city", "state", "postal_code", "country")
    autocomplete_fields = ("assigned_to",)
    change_form_template = "admin/ocpp/location/change_form.html"


@admin.register(EnergyTariff)
class EnergyTariffAdmin(EntityModelAdmin):
    list_display = (
        "contract_type_short",
        "zone",
        "period",
        "unit",
        "year",
        "price_mxn",
    )
    list_filter = ("year", "zone", "contract_type", "period", "season", "unit")
    search_fields = (
        "contract_type",
        "zone",
        "period",
        "season",
    )

    def get_model_perms(self, request):
        return {}

    @admin.display(description=_("Contract type"), ordering="contract_type")
    def contract_type_short(self, obj):
        match = re.search(r"\(([^)]+)\)", obj.get_contract_type_display())
        return match.group(1) if match else obj.get_contract_type_display()


class ClientReportRecurrencyFilter(admin.SimpleListFilter):
    title = "Recurrency"
    parameter_name = "recurrency"

    def lookups(self, request, model_admin):
        for value, label in ClientReportSchedule.PERIODICITY_CHOICES:
            yield (value, label)

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        if value == ClientReportSchedule.PERIODICITY_NONE:
            return queryset.filter(
                Q(schedule__isnull=True) | Q(schedule__periodicity=value)
            )
        return queryset.filter(schedule__periodicity=value)


@admin.register(ClientReport)
class ClientReportAdmin(EntityModelAdmin):
    list_display = (
        "created_on",
        "period_range",
        "owner",
        "recurrency_display",
        "total_kw_period_display",
        "download_link",
    )
    list_select_related = ("schedule", "owner")
    list_filter = ("owner", ClientReportRecurrencyFilter)
    readonly_fields = ("created_on", "data")

    change_list_template = "admin/core/clientreport/change_list.html"

    def period_range(self, obj):
        return str(obj)

    period_range.short_description = "Period"

    def recurrency_display(self, obj):
        return obj.periodicity_label

    recurrency_display.short_description = "Recurrency"

    def total_kw_period_display(self, obj):
        return f"{obj.total_kw_period:.2f}"

    total_kw_period_display.short_description = "Total kW (period)"

    def download_link(self, obj):
        url = reverse("admin:core_clientreport_download", args=[obj.pk])
        return format_html('<a href="{}">Download</a>', url)

    download_link.short_description = "Download"

    class ClientReportForm(forms.Form):
        PERIOD_CHOICES = [
            ("range", "Date range"),
            ("week", "Week"),
            ("month", "Month"),
        ]
        RECURRENCE_CHOICES = ClientReportSchedule.PERIODICITY_CHOICES
        VIEW_CHOICES = [
            ("expanded", _("Expanded view")),
            ("summary", _("Summarized view")),
        ]
        period = forms.ChoiceField(
            choices=PERIOD_CHOICES,
            widget=forms.RadioSelect,
            initial="range",
            help_text="Choose how the reporting window will be calculated.",
        )
        start_date = forms.DateField(required=False)
        end_date = forms.DateField(required=False)
        week = forms.CharField(required=False, help_text="yyyy-ww")
        month = forms.CharField(required=False, help_text="yyyy-mm")
        chargers = forms.ModelMultipleChoiceField(
            queryset=Charger.objects.all(),
            widget=forms.SelectMultiple,
            required=False,
        )
        recurrence = forms.ChoiceField(
            choices=RECURRENCE_CHOICES,
            required=False,
            initial=ClientReportSchedule.PERIODICITY_NONE,
            help_text="Select a cadence to automatically email new reports.",
        )
        email_recipients = forms.CharField(
            required=False,
            widget=forms.Textarea,
            help_text="Optional comma-separated email list for reports.",
        )
        disable_emails = forms.BooleanField(
            required=False,
            help_text="Generate the report without emailing recipients.",
        )
        title = forms.CharField(required=False, max_length=200)
        view_mode = forms.ChoiceField(
            choices=VIEW_CHOICES,
            required=False,
            initial="expanded",
            widget=forms.RadioSelect,
        )
        language = forms.ChoiceField(
            choices=settings.LANGUAGES,
            required=False,
            initial=ClientReport.default_language(),
        )

        def clean_week(self):
            week = self.cleaned_data["week"]
            if week:
                return ClientReport.normalize_week(week)
            return week

        def clean_month(self):
            month = self.cleaned_data["month"]
            if month:
                return ClientReport.normalize_month(month)
            return month

        def clean_title(self):
            title = self.cleaned_data.get("title")
            if not title:
                return ClientReport.default_title()
            return ClientReport.normalize_title(title)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "generate/",
                self.admin_site.admin_view(self.generate_view),
                name="core_clientreport_generate",
            ),
            path(
                "download/<int:report_id>/",
                self.admin_site.admin_view(self.download_view),
                name="core_clientreport_download",
            ),
        ]
        return custom + urls

    def generate_view(self, request):
        report = None
        report_rows = None
        schedule = None
        download_url = None
        form = self.ClientReportForm(request.POST or None)
        selected_chargers = Charger.objects.none()
        if form.is_bound and form.is_valid():
            start_date, end_date = ClientReport.resolve_period(
                form.cleaned_data.get("period"),
                form.cleaned_data.get("start_date"),
                form.cleaned_data.get("end_date"),
                form.cleaned_data.get("week"),
                form.cleaned_data.get("month"),
            )
            chargers = form.cleaned_data.get("chargers")
            selected_chargers = chargers if chargers is not None else Charger.objects.none()
            recipients_raw = form.cleaned_data.get("email_recipients") or ""
            recipients = [
                email.strip()
                for email in recipients_raw.split(",")
                if email.strip()
            ]
            disable_emails = form.cleaned_data.get("disable_emails")
            title = form.cleaned_data.get("title")
            language = form.cleaned_data.get("language")
            owner = request.user if request.user.is_authenticated else None
            report = ClientReport.generate(
                owner=owner,
                title=title,
                start_date=start_date,
                end_date=end_date,
                chargers=chargers,
                recipients=recipients,
                disable_emails=disable_emails,
                language=language,
                outbox=ClientReport.resolve_outbox_for_owner(owner),
                reply_to=ClientReport.resolve_reply_to_for_owner(owner),
            )
            report_rows = report.rows_for_display
            recurrence = form.cleaned_data.get("recurrence")
            if recurrence and recurrence != ClientReportSchedule.PERIODICITY_NONE:
                schedule = ClientReportSchedule.objects.create(
                    owner=owner,
                    created_by=request.user if request.user.is_authenticated else None,
                    periodicity=recurrence,
                    email_recipients=recipients,
                    disable_emails=disable_emails,
                    language=language,
                    title=title,
                )
                if chargers:
                    schedule.chargers.set(chargers)
                report.schedule = schedule
                report.save(update_fields=["schedule"])
                self.message_user(
                    request,
                    "Consumer report schedule created; future reports will be generated automatically.",
                    messages.SUCCESS,
                )
            if disable_emails:
                self.message_user(
                    request,
                    "Consumer report generated. The download will begin automatically.",
                    messages.SUCCESS,
                )
                redirect_url = f"{reverse('admin:core_clientreport_generate')}?download={report.pk}"
                return HttpResponseRedirect(redirect_url)
            report_rows = report.rows_for_display
            report_summary_rows = ClientReport.build_evcs_summary_rows(report_rows)
        else:
            report_summary_rows = None
            if form.is_bound:
                selected_chargers = form.cleaned_data.get("chargers") or Charger.objects.none()

        download_param = request.GET.get("download")
        if download_param:
            try:
                download_report = ClientReport.objects.get(pk=download_param)
            except ClientReport.DoesNotExist:
                pass
            else:
                download_url = reverse(
                    "admin:core_clientreport_download", args=[download_report.pk]
                )
        if report and report_rows is None:
            report_rows = report.rows_for_display
            report_summary_rows = ClientReport.build_evcs_summary_rows(report_rows)
        selected_view_mode = form.fields["view_mode"].initial
        if form.is_bound:
            if form.is_valid():
                selected_view_mode = form.cleaned_data.get(
                    "view_mode", selected_view_mode
                )
            else:
                selected_view_mode = form.data.get("view_mode", selected_view_mode)
        context = self.admin_site.each_context(request)
        context.update(
            {
                "form": form,
                "report": report,
                "schedule": schedule,
                "download_url": download_url,
                "opts": self.model._meta,
                "report_rows": report_rows,
                "report_summary_rows": report_summary_rows,
                "report_view_mode": selected_view_mode,
                "selected_chargers": selected_chargers,
            }
        )
        return TemplateResponse(
            request, "admin/core/clientreport/generate.html", context
        )

    def get_changelist_actions(self, request):
        parent = getattr(super(), "get_changelist_actions", None)
        actions: list[str] = []
        if callable(parent):
            parent_actions = parent(request)
            if parent_actions:
                actions.extend(parent_actions)
        if "generate_report" not in actions:
            actions.append("generate_report")
        return actions

    def generate_report(self, request):
        return HttpResponseRedirect(reverse("admin:core_clientreport_generate"))

    generate_report.label = _("Generate report")

    def download_view(self, request, report_id: int):
        report = get_object_or_404(ClientReport, pk=report_id)
        pdf_path = report.ensure_pdf()
        if not pdf_path.exists():
            raise Http404("Report file unavailable")
        end_date = report.end_date
        if hasattr(end_date, "isoformat"):
            end_date_str = end_date.isoformat()
        else:  # pragma: no cover - fallback for unexpected values
            end_date_str = str(end_date)
        filename = f"consumer-report-{end_date_str}.pdf"
        response = FileResponse(pdf_path.open("rb"), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
