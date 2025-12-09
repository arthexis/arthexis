from collections import defaultdict
import contextlib
from io import BytesIO
import os
from typing import Any

from django import forms
from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.urls import NoReverseMatch, path, reverse
from urllib.parse import urlencode, urlparse
from django.shortcuts import get_object_or_404, redirect, render
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    JsonResponse,
    HttpResponseBase,
    HttpResponseRedirect,
    HttpResponseNotAllowed,
)
from django.template.response import TemplateResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import EmailValidator
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.admin import (
    GroupAdmin as DjangoGroupAdmin,
    UserAdmin as DjangoUserAdmin,
)
import logging
from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.forms import (
    ConfirmImportForm,
    ImportForm,
    SelectableFieldsExportForm,
)
from import_export.widgets import ForeignKeyWidget
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.templatetags.static import static
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _, ngettext
from django.forms.models import BaseInlineFormSet
import json
import secrets
import uuid
import requests
import datetime
from django.db import IntegrityError, transaction
from django.db.models import Q
import calendar
import re
from django_object_actions import DjangoObjectActions
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from apps.ocpp.models import Charger, Transaction
from apps.vehicle.models import ElectricVehicle
from apps.rfids.utils import build_mode_toggle
from apps.emails.models import EmailCollector, EmailInbox, EmailOutbox
from apps.energy.models import ClientReport, CustomerAccount
from apps.repos.forms import PackageRepositoryForm
from apps.repos.task_utils import GitHubRepositoryError, create_repository_for_package
from apps.core.models import InviteLead
from apps.users.models import User, UserPhoneNumber
from apps.rfids.models import RFID
from apps.payments.models import OpenPayProcessor, PayPalProcessor, StripeProcessor
from apps.odoo.models import OdooEmployee, OdooProduct
from apps.locals.user_data import (
    EntityModelAdmin,
    UserDatumAdminMixin,
    delete_user_fixture,
    dump_user_fixture,
    _fixture_path,
    _resolve_fixture_user,
    _user_allows_user_data,
)
from .widgets import OdooProductWidget, RFIDDataWidget
from apps.rfids.rfid_import_export import (
    account_column_for_field,
    parse_accounts,
    serialize_accounts,
)
from apps.links.models import ExperienceReference, Reference
from apps.release import release as release_utils
from apps.users import temp_passwords

logger = logging.getLogger(__name__)


admin.site.unregister(Group)


def _append_operate_as(fieldsets):
    updated = []
    for name, options in fieldsets:
        opts = options.copy()
        fields = opts.get("fields")
        if fields and "is_staff" in fields and "operate_as" not in fields:
            if not isinstance(fields, (list, tuple)):
                fields = list(fields)
            else:
                fields = list(fields)
            fields.append("operate_as")
            opts["fields"] = tuple(fields)
        updated.append((name, opts))
    return tuple(updated)


def _include_require_2fa(fieldsets):
    updated = []
    for name, options in fieldsets:
        opts = options.copy()
        fields = list(opts.get("fields", ()))
        if "is_active" in fields and "require_2fa" not in fields:
            insert_at = fields.index("is_active") + 1
            fields.insert(insert_at, "require_2fa")
            opts["fields"] = tuple(fields)
        updated.append((name, opts))
    return tuple(updated)


def _include_temporary_expiration(fieldsets):
    updated = []
    for name, options in fieldsets:
        opts = options.copy()
        fields = list(opts.get("fields", ()))
        if "is_active" in fields and "temporary_expires_at" not in fields:
            insert_at = fields.index("is_active") + 1
            fields.insert(insert_at, "temporary_expires_at")
            opts["fields"] = tuple(fields)
        updated.append((name, opts))
    return tuple(updated)


def _include_site_template(fieldsets):
    updated = []
    inserted = False
    for name, options in fieldsets:
        opts = options.copy()
        fields = list(opts.get("fields", ()))
        if "groups" in fields and "site_template" not in fields:
            insert_at = fields.index("groups") + 1
            fields.insert(insert_at, "site_template")
            opts["fields"] = tuple(fields)
            inserted = True
        updated.append((name, opts))
    if not inserted:
        updated.append((_("Preferences"), {"fields": ("site_template",)}))
    return tuple(updated)


def _include_site_template_add(fieldsets):
    updated = []
    inserted = False
    for name, options in fieldsets:
        opts = options.copy()
        fields = list(opts.get("fields", ()))
        if "username" in fields and "site_template" not in fields:
            if "temporary_expires_at" in fields:
                insert_at = fields.index("temporary_expires_at") + 1
            else:
                insert_at = len(fields)
            fields.insert(insert_at, "site_template")
            opts["fields"] = tuple(fields)
            inserted = True
        updated.append((name, opts))
    if not inserted:
        updated.append((_("Preferences"), {"fields": ("site_template",)}))
    return tuple(updated)


# Add object links for small datasets in changelist view
original_changelist_view = admin.ModelAdmin.changelist_view


def changelist_view_with_object_links(self, request, extra_context=None):
    extra_context = extra_context or {}
    count = self.model._default_manager.count()
    if 1 <= count <= 4:
        links = []
        for obj in self.model._default_manager.all():
            url = reverse(
                f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_change",
                args=[obj.pk],
            )
            links.append({"url": url, "label": str(obj)})
        extra_context["global_object_links"] = links
    return original_changelist_view(self, request, extra_context=extra_context)


admin.ModelAdmin.changelist_view = changelist_view_with_object_links


_original_admin_get_app_list = admin.AdminSite.get_app_list

TEST_CREDENTIALS_LABEL = _("Test credentials")

GUEST_NAME_ADJECTIVES = (
    "brisk",
    "calm",
    "clever",
    "daring",
    "eager",
    "gentle",
    "honest",
    "lively",
    "merry",
    "nimble",
)

GUEST_NAME_NOUNS = (
    "badger",
    "heron",
    "lynx",
    "otter",
    "panda",
    "panther",
    "sparrow",
    "terrapin",
    "whale",
    "wren",
)


def _build_credentials_actions(action_name, handler_name, description=TEST_CREDENTIALS_LABEL):
    def bulk_action(self, request, queryset):
        handler = getattr(self, handler_name)
        for obj in queryset:
            handler(request, obj)

    bulk_action.__name__ = action_name
    bulk_action = admin.action(description=description)(bulk_action)
    bulk_action.__name__ = action_name

    def change_action(self, request, obj):
        getattr(self, handler_name)(request, obj)

    change_action.__name__ = f"{action_name}_action"
    change_action.label = description
    change_action.short_description = description
    return bulk_action, change_action


def get_app_list_with_protocol_forwarder(self, request, app_label=None):
    try:
        Application = django_apps.get_model("app", "Application")
    except LookupError:
        # Fall back to the original behavior if the Application model is unavailable.
        return _original_admin_get_app_list(self, request, app_label=app_label)

    full_list = list(_original_admin_get_app_list(self, request, app_label=None))
    result = full_list

    if app_label:
        result = [entry for entry in result if entry.get("app_label") == app_label]

    ordered_result = []

    for entry in result:
        app_label = entry.get("app_label")
        entry_name = str(app_label or entry.get("name"))

        ordered_entry = entry.copy()
        ordered_entry["name"] = Application.format_display_name(entry_name)
        ordered_result.append(ordered_entry)

    ordered_result.sort(key=lambda entry: (entry.get("name"), entry.get("app_label")))
    return ordered_result


admin.AdminSite.get_app_list = get_app_list_with_protocol_forwarder


class SaveBeforeChangeAction(DjangoObjectActions):
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(
            {
                "objectactions": [
                    self._get_tool_dict(action)
                    for action in self.get_change_actions(request, object_id, form_url)
                ],
                "tools_view_name": self.tools_view_name,
            }
        )
        return super().changeform_view(request, object_id, form_url, extra_context)

    def response_change(self, request, obj):
        action = request.POST.get("_action")
        if action:
            allowed = self.get_change_actions(request, str(obj.pk), None)
            if action in allowed and hasattr(self, action):
                response = getattr(self, action)(request, obj)
                if isinstance(response, HttpResponseBase):
                    return response
                return redirect(request.path)
        return super().response_change(request, obj)


class ProfileAdminMixin:
    """Reusable actions for profile-bound admin classes."""

    def _get_user_profile_info(self, request):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return user, None, 0

        group_ids = list(user.groups.values_list("id", flat=True))
        owner_filter = Q(user=user)
        if group_ids:
            owner_filter |= Q(group_id__in=group_ids)
        if hasattr(self.model, "avatar"):
            owner_filter |= Q(avatar__user=user)
            if group_ids:
                owner_filter |= Q(avatar__group_id__in=group_ids)

        queryset = self.model._default_manager.filter(owner_filter)
        profiles = list(queryset[:2])
        if not profiles:
            return user, None, 0
        if len(profiles) == 1:
            return user, profiles[0], 1
        return user, profiles[0], 2

    def get_my_profile_label(self, request):
        _user, profile, profile_count = self._get_user_profile_info(request)
        if profile_count == 0:
            return _("Active Profile (Unset)")
        if profile_count == 1 and profile is not None:
            return _("Active Profile (%(name)s)") % {"name": str(profile)}
        return _("Active Profile")

    def _resolve_my_profile_target(self, request):
        opts = self.model._meta
        changelist_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_changelist"
        )
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return (
                changelist_url,
                _("You must be logged in to manage your profile."),
                messages.ERROR,
            )

        _user, profile, profile_count = self._get_user_profile_info(request)
        if profile is not None:
            permission_check = getattr(self, "has_view_or_change_permission", None)
            has_permission = (
                permission_check(request, obj=profile)
                if callable(permission_check)
                else self.has_change_permission(request, obj=profile)
            )
            if has_permission:
                change_url = reverse(
                    f"admin:{opts.app_label}_{opts.model_name}_change",
                    args=[profile.pk],
                )
                return change_url, None, None
            return (
                changelist_url,
                _("You do not have permission to view this profile."),
                messages.ERROR,
            )

        if profile_count == 0 and self.has_add_permission(request):
            add_url = reverse(f"admin:{opts.app_label}_{opts.model_name}_add")
            params = {}
            user_id = getattr(user, "pk", None)
            if user_id:
                params["user"] = user_id
            if params:
                add_url = f"{add_url}?{urlencode(params)}"
            return add_url, None, None

        return (
            changelist_url,
            _("You do not have permission to create this profile."),
            messages.ERROR,
        )

    def get_my_profile_url(self, request):
        url, _message, _level = self._resolve_my_profile_target(request)
        return url

    def _redirect_to_my_profile(self, request):
        target_url, message, level = self._resolve_my_profile_target(request)
        if message:
            self.message_user(request, message, level=level)
        return HttpResponseRedirect(target_url)

    @admin.action(description=_("Active Profile"))
    def my_profile(self, request, queryset=None):
        return self._redirect_to_my_profile(request)

    def my_profile_action(self, request, obj=None):
        return self._redirect_to_my_profile(request)

    my_profile_action.label = _("Active Profile")
    my_profile_action.short_description = _("Active Profile")


class InviteLeadAdmin(EntityModelAdmin):
    list_display = (
        "email",
        "status",
        "assign_to",
        "mac_address",
        "created_on",
        "sent_on",
        "sent_via_outbox",
        "short_error",
    )
    list_filter = ("status",)
    search_fields = ("email", "comment")
    raw_id_fields = ("assign_to",)
    readonly_fields = (
        "created_on",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "mac_address",
        "sent_on",
        "sent_via_outbox",
        "error",
    )

    def short_error(self, obj):
        return (obj.error[:40] + "…") if len(obj.error) > 40 else obj.error

    short_error.short_description = "error"


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


class UserCreationWithExpirationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "temporary_expires_at", "site_template")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "temporary_expires_at" in self.fields:
            self.fields["temporary_expires_at"].required = False
        if "site_template" in self.fields:
            self.fields["site_template"].required = False


class UserChangeRFIDForm(forms.ModelForm):
    """Admin change form exposing login RFID assignment."""

    login_rfid = forms.ModelChoiceField(
        label=_("Login RFID"),
        queryset=RFID.objects.none(),
        required=False,
        help_text=_("Assign an RFID card to this user for RFID logins."),
    )

    class Meta:
        model = User
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance
        field = self.fields["login_rfid"]
        account = getattr(user, "customer_account", None)
        if account is not None:
            queryset = RFID.objects.filter(
                Q(energy_accounts__isnull=True) | Q(energy_accounts=account)
            )
            current = account.rfids.order_by("label_id").first()
            if current:
                field.initial = current.pk
        else:
            queryset = RFID.objects.filter(energy_accounts__isnull=True)
        field.queryset = queryset.order_by("label_id")
        field.empty_label = _("Keep current assignment")

    def _ensure_customer_account(self, user):
        account = getattr(user, "customer_account", None)
        if account is not None:
            if account.user_id != user.pk:
                account.user = user
                account.save(update_fields=["user"])
            return account
        account = CustomerAccount.objects.filter(user=user).first()
        if account is not None:
            if account.user_id != user.pk:
                account.user = user
                account.save(update_fields=["user"])
            return account
        base_slug = slugify(
            user.username
            or user.get_full_name()
            or user.email
            or (str(user.pk) if user.pk is not None else "")
        )
        if not base_slug:
            base_slug = f"user-{uuid.uuid4().hex[:8]}"
        base_name = base_slug.upper()
        candidate = base_name
        suffix = 1
        while CustomerAccount.objects.filter(name=candidate).exists():
            suffix += 1
            candidate = f"{base_name}-{suffix}"
        return CustomerAccount.objects.create(user=user, name=candidate)

    def save(self, commit=True):
        user = super().save(commit)
        rfid = self.cleaned_data.get("login_rfid")
        if not rfid:
            return user
        account = self._ensure_customer_account(user)
        if account.pk is None:
            account.save()
        other_accounts = list(rfid.energy_accounts.exclude(pk=account.pk))
        if other_accounts:
            rfid.energy_accounts.remove(*other_accounts)
        if not account.rfids.filter(pk=rfid.pk).exists():
            account.rfids.add(rfid)
        return user


def _raw_instance_value(instance, field_name):
    """Return the stored value for ``field_name`` without resolving sigils."""

    field = instance._meta.get_field(field_name)
    if not instance.pk:
        return field.value_from_object(instance)
    manager = type(instance)._default_manager
    try:
        return (
            manager.filter(pk=instance.pk).values_list(field.attname, flat=True).get()
        )
    except type(instance).DoesNotExist:  # pragma: no cover - instance deleted
        return field.value_from_object(instance)


class KeepExistingValue:
    """Sentinel indicating a field should retain its stored value."""

    __slots__ = ("field",)

    def __init__(self, field: str):
        self.field = field

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return False

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<KeepExistingValue field={self.field!r}>"


def keep_existing(field: str) -> KeepExistingValue:
    return KeepExistingValue(field)


def _restore_sigil_values(form, field_names):
    """Reset sigil fields on ``form.instance`` to their raw form values."""

    for name in field_names:
        if name not in form.fields:
            continue
        if name in form.cleaned_data:
            raw = form.cleaned_data[name]
            if isinstance(raw, KeepExistingValue):
                raw = _raw_instance_value(form.instance, name)
        else:
            raw = _raw_instance_value(form.instance, name)
        setattr(form.instance, name, raw)


class OdooEmployeeAdminForm(forms.ModelForm):
    """Admin form for :class:`core.models.OdooEmployee` with hidden password."""

    password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave blank to keep the current password.",
    )

    class Meta:
        model = OdooEmployee
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["password"].initial = ""
            self.initial["password"] = ""
        else:
            self.fields["password"].required = True

    def clean_password(self):
        pwd = self.cleaned_data.get("password")
        if not pwd and self.instance.pk:
            return keep_existing("password")
        return pwd

    def _post_clean(self):
        super()._post_clean()
        _restore_sigil_values(
            self,
            ["host", "database", "username", "password"],
        )


class PaymentProcessorAdminForm(forms.ModelForm):
    masked_fields: tuple[str, ...] = ()
    sigil_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            for field in self.masked_fields:
                if field in self.fields:
                    self.fields[field].initial = ""
                    self.initial[field] = ""

    @staticmethod
    def _has_value(value) -> bool:
        if isinstance(value, KeepExistingValue):
            return True
        if isinstance(value, bool):
            return value
        return value not in (None, "", [], (), {}, set())

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE"):
            return cleaned
        if self.instance.pk:
            for field in self.masked_fields:
                if cleaned.get(field) == "":
                    cleaned[field] = keep_existing(field)
        return cleaned

    def _post_clean(self):
        super()._post_clean()
        if self.sigil_fields:
            _restore_sigil_values(self, list(self.sigil_fields))


class OpenPayProcessorAdminForm(PaymentProcessorAdminForm):
    masked_fields = ("private_key", "webhook_secret")
    sigil_fields = ("merchant_id", "private_key", "public_key", "webhook_secret")

    class Meta:
        model = OpenPayProcessor
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["merchant_id"].help_text = _(
            "Provide merchant ID, public and private keys, and webhook secret from OpenPay."
        )
        self.fields["public_key"].help_text = _(
            "OpenPay public key used for browser integrations."
        )
        self.fields["private_key"].help_text = _(
            "OpenPay private key used for server-side requests. Leave blank to keep the current key."
        )
        self.fields["webhook_secret"].help_text = _(
            "Secret used to sign OpenPay webhooks. Leave blank to keep the current secret."
        )
        self.fields["is_production"].help_text = _(
            "Enable to send requests to OpenPay's live environment."
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE") or self.errors:
            return cleaned

        required = ("merchant_id", "private_key", "public_key")
        provided = [name for name in required if self._has_value(cleaned.get(name))]
        missing = [name for name in required if not self._has_value(cleaned.get(name))]
        if provided and missing:
            raise forms.ValidationError(
                _("Provide merchant ID, private key, and public key to configure OpenPay.")
            )
        if not provided:
            raise forms.ValidationError(
                _("Provide merchant ID, private key, and public key to configure OpenPay.")
            )
        return cleaned


class PayPalProcessorAdminForm(PaymentProcessorAdminForm):
    masked_fields = ("client_secret",)
    sigil_fields = ("client_id", "client_secret", "webhook_id")

    class Meta:
        model = PayPalProcessor
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client_id"].help_text = _("PayPal REST client ID for your application.")
        self.fields["client_secret"].help_text = _(
            "PayPal REST client secret. Leave blank to keep the current secret."
        )
        self.fields["webhook_id"].help_text = _(
            "PayPal webhook ID used to validate notifications. Leave blank to keep the current webhook identifier."
        )
        self.fields["is_production"].help_text = _(
            "Enable to send requests to PayPal's live environment."
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE") or self.errors:
            return cleaned
        required = ("client_id", "client_secret")
        provided = [name for name in required if self._has_value(cleaned.get(name))]
        if len(provided) != len(required):
            raise forms.ValidationError(
                _("Provide PayPal client ID and client secret to configure PayPal.")
            )
        return cleaned


class StripeProcessorAdminForm(PaymentProcessorAdminForm):
    masked_fields = ("secret_key", "webhook_secret")
    sigil_fields = ("secret_key", "publishable_key", "webhook_secret")

    class Meta:
        model = StripeProcessor
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["secret_key"].help_text = _(
            "Stripe secret key used for authenticated API requests. Leave blank to keep the current key."
        )
        self.fields["publishable_key"].help_text = _(
            "Stripe publishable key used by client integrations."
        )
        self.fields["webhook_secret"].help_text = _(
            "Secret used to validate Stripe webhook signatures. Leave blank to keep the current secret."
        )
        self.fields["is_production"].help_text = _(
            "Enable to mark Stripe as live mode; disable for test mode."
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE") or self.errors:
            return cleaned
        required = ("secret_key", "publishable_key")
        provided = [name for name in required if self._has_value(cleaned.get(name))]
        if len(provided) != len(required):
            raise forms.ValidationError(
                _("Provide Stripe secret and publishable keys to configure Stripe.")
            )
        return cleaned


class MaskedPasswordFormMixin:
    """Mixin that hides stored passwords while allowing updates."""

    password_sigil_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("password")
        if field is None:
            return
        if not isinstance(field.widget, forms.PasswordInput):
            field.widget = forms.PasswordInput()
        field.widget.attrs.setdefault("autocomplete", "new-password")
        field.help_text = field.help_text or "Leave blank to keep the current password."
        if self.instance.pk:
            field.initial = ""
            self.initial["password"] = ""
        else:
            field.required = True

    def clean_password(self):
        field = self.fields.get("password")
        if field is None:
            return self.cleaned_data.get("password")
        pwd = self.cleaned_data.get("password")
        if not pwd and self.instance.pk:
            return keep_existing("password")
        return pwd

    def _post_clean(self):
        super()._post_clean()
        if self.password_sigil_fields:
            _restore_sigil_values(self, self.password_sigil_fields)


class EmailInboxAdminForm(MaskedPasswordFormMixin, forms.ModelForm):
    """Admin form for :class:`apps.emails.models.EmailInbox` with hidden password."""

    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        required=False,
        help_text="Leave blank to keep the current password.",
    )
    password_sigil_fields = ("username", "host", "password", "protocol")

    class Meta:
        model = EmailInbox
        fields = "__all__"


class ProfileInlineFormSet(BaseInlineFormSet):
    """Hide deletion controls and allow implicit removal when empty."""

    @classmethod
    def get_default_prefix(cls):
        prefix = super().get_default_prefix()
        if prefix:
            return prefix
        model_name = cls.model._meta.model_name
        remote_field = getattr(cls.fk, "remote_field", None)
        if remote_field is not None and getattr(remote_field, "one_to_one", False):
            return model_name
        return f"{model_name}_set"

    def add_fields(self, form, index):
        super().add_fields(form, index)
        if "DELETE" in form.fields:
            form.fields["DELETE"].widget = forms.HiddenInput()
            form.fields["DELETE"].required = False


def _title_case(value):
    text = str(value or "")
    return " ".join(
        word[:1].upper() + word[1:] if word else word for word in text.split()
    )


class ProfileFormMixin(forms.ModelForm):
    """Mark profiles for deletion when no data is provided."""

    profile_fields: tuple[str, ...] = ()
    user_datum = forms.BooleanField(
        required=False,
        label=_("User Datum"),
        help_text=_("Store this profile in the user's data directory."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        model_fields = getattr(self._meta.model, "profile_fields", tuple())
        explicit = getattr(self, "profile_fields", tuple())
        self._profile_fields = tuple(explicit or model_fields)
        for name in self._profile_fields:
            field = self.fields.get(name)
            if field is not None:
                field.required = False
        if "user_datum" in self.fields:
            self.fields["user_datum"].initial = getattr(
                self.instance, "is_user_data", False
            )

    @staticmethod
    def _is_empty_value(value) -> bool:
        if isinstance(value, KeepExistingValue):
            return True
        if isinstance(value, bool):
            return not value
        if value in (None, "", [], (), {}, set()):
            return True
        if isinstance(value, str):
            return value.strip() == ""
        return False

    def _has_profile_data(self) -> bool:
        for name in self._profile_fields:
            field = self.fields.get(name)
            raw_value = None
            if field is not None and not isinstance(field, forms.BooleanField):
                try:
                    if hasattr(self, "_raw_value"):
                        raw_value = self._raw_value(name)
                    elif self.is_bound:
                        bound = self[name]
                        raw_value = bound.field.widget.value_from_datadict(
                            self.data,
                            self.files,
                            bound.html_name,
                        )
                except (AttributeError, KeyError):
                    raw_value = None
            if raw_value is not None:
                if not isinstance(raw_value, (list, tuple)):
                    values = [raw_value]
                else:
                    values = raw_value
                if any(not self._is_empty_value(value) for value in values):
                    return True
                # When raw form data is present but empty (e.g. ""), skip the
                # instance fallback so empty submissions mark the form deleted.
                continue

            if self.is_bound and name not in self.cleaned_data:
                continue

            if name in self.cleaned_data:
                value = self.cleaned_data.get(name)
            elif hasattr(self.instance, name):
                value = getattr(self.instance, name)
            else:
                continue
            if not self._is_empty_value(value):
                return True
        return False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE") or not self._profile_fields:
            return cleaned
        if not self._has_profile_data():
            cleaned["DELETE"] = True
        return cleaned


class OdooEmployeeInlineForm(ProfileFormMixin, OdooEmployeeAdminForm):
    profile_fields = OdooEmployee.profile_fields

    class Meta(OdooEmployeeAdminForm.Meta):
        exclude = ("user", "group", "verified_on", "odoo_uid", "name", "email")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE") or self.errors:
            return cleaned

        provided = [
            name
            for name in self._profile_fields
            if not self._is_empty_value(cleaned.get(name))
        ]
        missing = [
            name
            for name in self._profile_fields
            if self._is_empty_value(cleaned.get(name))
        ]
        if provided and missing:
            raise forms.ValidationError(
                "Provide host, database, username, and password to create an Odoo employee.",
            )

        return cleaned


class EmailInboxInlineForm(ProfileFormMixin, EmailInboxAdminForm):
    profile_fields = EmailInbox.profile_fields

    class Meta(EmailInboxAdminForm.Meta):
        exclude = ("user", "group")


class EmailOutboxAdminForm(MaskedPasswordFormMixin, forms.ModelForm):
    """Admin form for :class:`apps.emails.models.EmailOutbox` with hidden password."""

    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        required=False,
        help_text="Leave blank to keep the current password.",
    )
    priority = forms.IntegerField(
        required=False,
        initial=0,
        help_text="Higher values are selected first when multiple outboxes are available.",
    )
    password_sigil_fields = ("password", "host", "username", "from_email")

    class Meta:
        model = EmailOutbox
        fields = "__all__"

    def clean_priority(self):
        value = self.cleaned_data.get("priority")
        return 0 if value in (None, "") else value


class EmailOutboxInlineForm(ProfileFormMixin, EmailOutboxAdminForm):
    profile_fields = EmailOutbox.profile_fields

    class Meta(EmailOutboxAdminForm.Meta):
        fields = (
            "password",
            "host",
            "port",
            "username",
            "use_tls",
            "use_ssl",
            "from_email",
            "is_enabled",
        )




PROFILE_INLINE_CONFIG = {
    OdooEmployee: {
        "form": OdooEmployeeInlineForm,
        "fieldsets": (
            (
                None,
                {
                    "fields": (
                        "host",
                        "database",
                        "username",
                        "password",
                    )
                },
            ),
            (
                "Odoo Employee",
                {
                    "fields": ("verified_on", "odoo_uid", "name", "email"),
                },
            ),
        ),
        "readonly_fields": ("verified_on", "odoo_uid", "name", "email"),
    },
    EmailInbox: {
        "form": EmailInboxInlineForm,
        "fields": (
            "username",
            "host",
            "port",
            "password",
            "protocol",
            "use_ssl",
            "is_enabled",
            "priority",
        ),
    },
    EmailOutbox: {
        "form": EmailOutboxInlineForm,
        "fields": (
            "password",
            "host",
            "port",
            "username",
            "use_tls",
            "use_ssl",
            "from_email",
        ),
    },
}


def _build_profile_inline(model, owner_field):
    config = PROFILE_INLINE_CONFIG[model]
    verbose_name = config.get("verbose_name")
    if verbose_name is None:
        verbose_name = _title_case(model._meta.verbose_name)
    verbose_name_plural = config.get("verbose_name_plural")
    if verbose_name_plural is None:
        verbose_name_plural = _title_case(model._meta.verbose_name_plural)
    attrs = {
        "model": model,
        "fk_name": owner_field,
        "form": config["form"],
        "formset": ProfileInlineFormSet,
        "extra": 1,
        "max_num": 1,
        "can_delete": True,
        "verbose_name": verbose_name,
        "verbose_name_plural": verbose_name_plural,
        "template": "admin/edit_inline/profile_stacked.html",
        "fieldset_visibility": tuple(config.get("fieldset_visibility", ())),
    }
    if "fieldsets" in config:
        attrs["fieldsets"] = config["fieldsets"]
    if "fields" in config:
        attrs["fields"] = config["fields"]
    if "readonly_fields" in config:
        attrs["readonly_fields"] = config["readonly_fields"]
    if "template" in config:
        attrs["template"] = config["template"]
    return type(
        f"{model.__name__}{owner_field.title()}Inline",
        (admin.StackedInline,),
        attrs,
    )


PROFILE_MODELS = (
    OdooEmployee,
    EmailInbox,
    EmailOutbox,
)
USER_PROFILE_INLINES = [
    _build_profile_inline(model, "user") for model in PROFILE_MODELS
]
GROUP_PROFILE_INLINES = [
    _build_profile_inline(model, "group") for model in PROFILE_MODELS
]


class UserPhoneNumberInline(admin.TabularInline):
    model = UserPhoneNumber
    extra = 0
    fields = ("number", "priority")


@admin.register(User)
class UserAdmin(UserDatumAdminMixin, DjangoUserAdmin):
    form = UserChangeRFIDForm
    add_form = UserCreationWithExpirationForm
    actions = (DjangoUserAdmin.actions or []) + ["login_as_guest_user"]
    changelist_actions = ["login_as_guest_user"]
    fieldsets = _include_site_template(
        _include_temporary_expiration(
            _include_require_2fa(_append_operate_as(DjangoUserAdmin.fieldsets))
        )
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "temporary_expires_at",
                    "password1",
                    "password2",
                ),
            },
        ),
    )
    add_fieldsets = _include_site_template_add(
        _include_temporary_expiration(
            _include_require_2fa(_append_operate_as(add_fieldsets))
        )
    )
    inlines = USER_PROFILE_INLINES + [UserPhoneNumberInline]
    change_form_template = "admin/user_profile_change_form.html"
    _skip_entity_user_datum = True

    def _generate_guest_username(self) -> str:
        attempts = 0
        candidate = None
        while attempts < 10:
            candidate = f"{secrets.choice(GUEST_NAME_ADJECTIVES)}-{secrets.choice(GUEST_NAME_NOUNS)}"
            if not self.model.objects.filter(username=candidate).exists():
                return candidate
            attempts += 1
        suffix = secrets.token_hex(2)
        return f"{candidate}-{suffix}" if candidate else f"guest-{suffix}"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "login-as-guest/",
                self.admin_site.admin_view(self.login_as_guest_user),
                name="core_user_login_as_guest_user",
            )
        ]
        return custom + urls

    def get_changelist_actions(self, request):
        parent = getattr(super(), "get_changelist_actions", None)
        actions = []
        if callable(parent):
            parent_actions = parent(request)
            if parent_actions:
                actions.extend(parent_actions)
        if "login_as_guest_user" not in actions:
            actions.append("login_as_guest_user")
        return actions

    @admin.action(description=_("Login as Guest User"), permissions=["add"])
    def login_as_guest_user(self, request, queryset=None):
        if not self.has_add_permission(request):
            raise PermissionDenied

        expires_at = timezone.now() + temp_passwords.DEFAULT_EXPIRATION
        username = self._generate_guest_username()
        guest_user = self.model.objects.create_user(
            username=username,
            password=None,
            is_staff=True,
            is_superuser=False,
            require_2fa=False,
            temporary_expires_at=expires_at,
        )

        temp_password = temp_passwords.generate_password()
        entry = temp_passwords.store_temp_password(
            guest_user.username, temp_password, expires_at=expires_at
        )

        login(request, guest_user, backend="apps.users.backends.TempPasswordBackend")

        expires_display = timezone.localtime(entry.expires_at)
        expires_label = expires_display.strftime("%Y-%m-%d %H:%M %Z")
        self.message_user(
            request,
            _(
                "Logged in as %(username)s with temporary password %(password)s (expires %(expires)s)."
            )
            % {
                "username": guest_user.username,
                "password": temp_password,
                "expires": expires_label,
            },
            messages.WARNING,
        )

        redirect_url = request.GET.get("next") or reverse("admin:index")
        return HttpResponseRedirect(redirect_url)

    login_as_guest_user.label = _("Login as Guest User")
    login_as_guest_user.short_description = _("Login as Guest User")
    login_as_guest_user.requires_queryset = False

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        if obj is not None and fieldsets:
            name, options = fieldsets[0]
            fields = list(options.get("fields", ()))
            if "login_rfid" not in fields:
                fields.append("login_rfid")
                options = options.copy()
                options["fields"] = tuple(fields)
                fieldsets[0] = (name, options)
        return fieldsets

    def _get_operate_as_profile_template(self):
        opts = self.model._meta
        try:
            return reverse(
                f"{self.admin_site.name}:{opts.app_label}_{opts.model_name}_change",
                args=["__ID__"],
            )
        except NoReverseMatch:
            user_opts = User._meta
            try:
                return reverse(
                    f"{self.admin_site.name}:{user_opts.app_label}_{user_opts.model_name}_change",
                    args=["__ID__"],
                )
            except NoReverseMatch:
                return None

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        response = super().render_change_form(
            request, context, add=add, change=change, form_url=form_url, obj=obj
        )
        if isinstance(response, dict):
            context_data = response
        else:
            context_data = getattr(response, "context_data", None)
        if context_data is not None:
            context_data["show_user_datum"] = False
            context_data["show_seed_datum"] = False
            context_data["show_save_as_copy"] = False
        operate_as_user = None
        operate_as_template = self._get_operate_as_profile_template()
        operate_as_url = None
        if obj and getattr(obj, "operate_as_id", None):
            try:
                operate_as_user = obj.operate_as
            except User.DoesNotExist:
                operate_as_user = None
            if operate_as_user and operate_as_template:
                operate_as_url = operate_as_template.replace(
                    "__ID__", str(operate_as_user.pk)
                )
        if context_data is not None:
            context_data["operate_as_user"] = operate_as_user
            context_data["operate_as_profile_url_template"] = operate_as_template
            context_data["operate_as_profile_url"] = operate_as_url
        return response

    def get_inline_instances(self, request, obj=None):
        inline_instances = super().get_inline_instances(request, obj)
        if obj and getattr(obj, "is_profile_restricted", False):
            profile_inline_classes = tuple(USER_PROFILE_INLINES)
            inline_instances = [
                inline
                for inline in inline_instances
                if inline.__class__ not in profile_inline_classes
            ]
        return inline_instances

    def _update_profile_fixture(self, instance, owner, *, store: bool) -> None:
        if not getattr(instance, "pk", None):
            return
        manager = getattr(type(instance), "all_objects", None)
        if manager is not None:
            manager.filter(pk=instance.pk).update(is_user_data=store)
        instance.is_user_data = store
        if owner is None:
            owner = getattr(instance, "user", None)
        if owner is None:
            return
        if store:
            dump_user_fixture(instance, owner)
        else:
            delete_user_fixture(instance, owner)

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        owner = form.instance if isinstance(form.instance, User) else None
        for deleted in getattr(formset, "deleted_objects", []):
            owner_user = getattr(deleted, "user", None) or owner
            self._update_profile_fixture(deleted, owner_user, store=False)
        for inline_form in getattr(formset, "forms", []):
            if not hasattr(inline_form, "cleaned_data"):
                continue
            if inline_form.cleaned_data.get("DELETE"):
                continue
            if "user_datum" not in inline_form.cleaned_data:
                continue
            instance = inline_form.instance
            owner_user = getattr(instance, "user", None) or owner
            should_store = bool(inline_form.cleaned_data.get("user_datum"))
            self._update_profile_fixture(instance, owner_user, store=should_store)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not getattr(obj, "pk", None):
            return
        target_user = _resolve_fixture_user(obj, obj)
        allow_user_data = _user_allows_user_data(target_user)
        if request.POST.get("_user_datum") == "on":
            type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=False)
            obj.is_user_data = False
            delete_user_fixture(obj, target_user)
            self.message_user(
                request,
                _("User data for user accounts is managed through the profile sections."),
            )
        elif obj.is_user_data:
            type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=False)
            obj.is_user_data = False
            delete_user_fixture(obj, target_user)


class EmailCollectorInline(admin.TabularInline):
    model = EmailCollector
    extra = 0
    fields = ("name", "subject", "sender")


class EmailCollectorAdmin(EntityModelAdmin):
    list_display = ("name", "inbox", "subject", "sender", "body", "fragment")
    search_fields = ("name", "subject", "sender", "body", "fragment")
    actions = ["preview_messages"]

    @admin.action(description=_("Preview matches"))
    def preview_messages(self, request, queryset):
        results = []
        for collector in queryset.select_related("inbox"):
            try:
                messages = collector.search_messages(limit=5)
                error = None
            except ValidationError as exc:
                messages = []
                error = str(exc)
            except Exception as exc:  # pragma: no cover - admin feedback
                messages = []
                error = str(exc)
            results.append(
                {
                    "collector": collector,
                    "messages": messages,
                    "error": error,
                }
            )
        context = {
            "title": _("Preview Email Collectors"),
            "results": results,
            "opts": self.model._meta,
            "queryset": queryset,
        }
        return TemplateResponse(
            request, "admin/core/emailcollector/preview.html", context
        )


@admin.register(OdooEmployee)
class OdooEmployeeAdmin(ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin):
    change_form_template = "django_object_actions/change_form.html"
    form = OdooEmployeeAdminForm
    list_display = ("owner", "host", "database", "credentials_ok", "verified_on")
    list_filter = ()
    readonly_fields = ("verified_on", "odoo_uid", "name", "email", "partner_id")
    actions = ["verify_credentials"]
    change_actions = ["verify_credentials_action", "my_profile_action"]
    changelist_actions = ["my_profile", "generate_quote_report"]
    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        ("Configuration", {"fields": ("host", "database")}),
        ("Credentials", {"fields": ("username", "password")}),
        (
            "Odoo Employee",
            {"fields": ("verified_on", "odoo_uid", "name", "email", "partner_id")},
        ),
    )

    def owner(self, obj):
        return obj.owner_display()

    owner.short_description = "Owner"

    @admin.display(description=_("Credentials OK"), boolean=True)
    def credentials_ok(self, obj):
        return bool(obj.password) and obj.is_verified

    def _verify_credentials(self, request, profile):
        try:
            profile.verify()
            self.message_user(request, f"{profile.owner_display()} verified")
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request, f"{profile.owner_display()}: {exc}", level=messages.ERROR
            )

    def generate_quote_report(self, request, queryset=None):
        return HttpResponseRedirect(reverse("odoo-quote-report"))

    generate_quote_report.label = _("Quote Report")
    generate_quote_report.short_description = _("Quote Report")

    (
        verify_credentials,
        verify_credentials_action,
    ) = _build_credentials_actions("verify_credentials", "_verify_credentials")


class PaymentProcessorAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    change_form_template = "django_object_actions/change_form.html"
    readonly_fields = ("verified_on", "verification_reference")
    actions = ["verify_credentials"]
    change_actions = ["verify_credentials_action"]

    @admin.display(description=_("Payment Processor"))
    def display_name(self, obj):
        return obj.identifier()

    def _verify_credentials(self, request, profile):
        identifier = profile.identifier()
        try:
            profile.verify()
        except ValidationError as exc:
            message = "; ".join(exc.messages)
            self.message_user(
                request,
                f"{identifier}: {message}",
                level=messages.ERROR,
            )
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request,
                f"{identifier}: {exc}",
                level=messages.ERROR,
            )
        else:
            self.message_user(
                request,
                _("%(name)s verified") % {"name": identifier},
                level=messages.SUCCESS,
            )

    (
        verify_credentials,
        verify_credentials_action,
    ) = _build_credentials_actions("verify_credentials", "_verify_credentials")


@admin.register(OpenPayProcessor)
class OpenPayProcessorAdmin(PaymentProcessorAdmin):
    form = OpenPayProcessorAdminForm
    list_display = ("display_name", "environment", "verified_on")
    fieldsets = (
        (
            _("OpenPay"),
            {
                "fields": (
                    "merchant_id",
                    "public_key",
                    "private_key",
                    "webhook_secret",
                    "is_production",
                ),
                "description": _("Configure OpenPay merchant access."),
            },
        ),
        (
            _("Verification"),
            {"fields": ("verified_on", "verification_reference")},
        ),
    )

    @admin.display(description=_("Environment"))
    def environment(self, obj):
        return _("OpenPay Production") if obj.is_production else _("OpenPay Sandbox")


@admin.register(PayPalProcessor)
class PayPalProcessorAdmin(PaymentProcessorAdmin):
    form = PayPalProcessorAdminForm
    list_display = ("display_name", "environment", "verified_on")
    fieldsets = (
        (
            _("PayPal"),
            {
                "fields": (
                    "client_id",
                    "client_secret",
                    "webhook_id",
                    "is_production",
                ),
                "description": _("Configure PayPal REST API access."),
            },
        ),
        (
            _("Verification"),
            {"fields": ("verified_on", "verification_reference")},
        ),
    )

    @admin.display(description=_("Environment"))
    def environment(self, obj):
        return _("PayPal Production") if obj.is_production else _("PayPal Sandbox")


@admin.register(StripeProcessor)
class StripeProcessorAdmin(PaymentProcessorAdmin):
    form = StripeProcessorAdminForm
    list_display = ("display_name", "environment", "verified_on")
    fieldsets = (
        (
            _("Stripe"),
            {
                "fields": (
                    "secret_key",
                    "publishable_key",
                    "webhook_secret",
                    "is_production",
                ),
                "description": _("Configure Stripe API access."),
            },
        ),
        (
            _("Verification"),
            {"fields": ("verified_on", "verification_reference")},
        ),
    )

    @admin.display(description=_("Environment"))
    def environment(self, obj):
        return _("Stripe Live") if obj.is_production else _("Stripe Test")


class EmailSearchForm(forms.Form):
    subject = forms.CharField(
        required=False, widget=forms.TextInput(attrs={"style": "width: 40em;"})
    )
    from_address = forms.CharField(
        label="From",
        required=False,
        widget=forms.TextInput(attrs={"style": "width: 40em;"}),
    )
    body = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"style": "width: 40em; height: 10em;"}),
    )


class EmailInboxAdmin(ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin):
    form = EmailInboxAdminForm
    list_display = ("owner_label", "username", "host", "protocol", "is_enabled")
    actions = ["test_connection", "search_inbox", "test_collectors"]
    change_actions = ["test_collectors_action", "my_profile_action"]
    changelist_actions = ["my_profile"]
    change_form_template = "admin/core/emailinbox/change_form.html"
    inlines = [EmailCollectorInline]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/test/",
                self.admin_site.admin_view(self.test_inbox),
                name="emails_emailinbox_test",
            )
        ]
        return custom + urls

    def test_inbox(self, request, object_id):
        inbox = self.get_object(request, object_id)
        if not inbox:
            self.message_user(request, "Unknown inbox", messages.ERROR)
            return redirect("..")
        try:
            inbox.test_connection()
            self.message_user(request, "Inbox connection successful", messages.SUCCESS)
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(request, str(exc), messages.ERROR)
        return redirect("..")

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        if object_id:
            extra_context["test_url"] = reverse(
                "admin:emails_emailinbox_test", args=[object_id]
            )
        return super().changeform_view(request, object_id, form_url, extra_context)

    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        ("Credentials", {"fields": ("username", "password")}),
        (
            "Configuration",
            {"fields": ("host", "port", "protocol", "use_ssl", "is_enabled", "priority")},
        ),
    )

    @admin.display(description="Owner")
    def owner_label(self, obj):
        return obj.owner_display()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

    @admin.action(description="Test selected inboxes")
    def test_connection(self, request, queryset):
        for inbox in queryset:
            try:
                inbox.test_connection()
                self.message_user(request, f"{inbox} connection successful")
            except Exception as exc:  # pragma: no cover - admin feedback
                self.message_user(request, f"{inbox}: {exc}", level=messages.ERROR)

    def _test_collectors(self, request, inbox):
        for collector in inbox.collectors.all():
            before = collector.artifacts.count()
            try:
                collector.collect(limit=1)
                after = collector.artifacts.count()
                if after > before:
                    msg = f"{collector} collected {after - before} email(s)"
                    self.message_user(request, msg)
                else:
                    self.message_user(
                        request, f"{collector} found no emails", level=messages.WARNING
                    )
            except Exception as exc:  # pragma: no cover - admin feedback
                self.message_user(request, f"{collector}: {exc}", level=messages.ERROR)

    @admin.action(description="Test collectors")
    def test_collectors(self, request, queryset):
        for inbox in queryset:
            self._test_collectors(request, inbox)

    def test_collectors_action(self, request, obj):
        self._test_collectors(request, obj)

    test_collectors_action.label = "Test collectors"
    test_collectors_action.short_description = "Test collectors"

    @admin.action(description="Search selected inbox")
    def search_inbox(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request, "Please select exactly one inbox.", level=messages.ERROR
            )
            return None
        inbox = queryset.first()
        if request.POST.get("apply"):
            form = EmailSearchForm(request.POST)
            if form.is_valid():
                results = inbox.search_messages(
                    subject=form.cleaned_data["subject"],
                    from_address=form.cleaned_data["from_address"],
                    body=form.cleaned_data["body"],
                    use_regular_expressions=False,
                )
                context = {
                    "form": form,
                    "results": results,
                    "queryset": queryset,
                    "action": "search_inbox",
                    "opts": self.model._meta,
                }
                return TemplateResponse(
                    request, "admin/core/emailinbox/search.html", context
                )
        else:
            form = EmailSearchForm()
        context = {
            "form": form,
            "queryset": queryset,
            "action": "search_inbox",
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/core/emailinbox/search.html", context)


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


class OdooProductAdminForm(forms.ModelForm):
    class Meta:
        model = OdooProduct
        fields = "__all__"
        widgets = {"odoo_product": OdooProductWidget}


@admin.register(OdooProduct)
class OdooProductAdmin(EntityModelAdmin):
    form = OdooProductAdminForm
    actions = ["register_from_odoo"]
    change_list_template = "admin/core/product/change_list.html"

    def _odoo_employee_admin(self):
        return self.admin_site._registry.get(OdooEmployee)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "register-from-odoo/",
                self.admin_site.admin_view(self.register_from_odoo_view),
                name=f"{self.opts.app_label}_{self.opts.model_name}_register_from_odoo",
            )
        ]
        return custom + urls

    @admin.action(description="Register from Odoo")
    def register_from_odoo(self, request, queryset=None):  # pragma: no cover - simple redirect
        return HttpResponseRedirect(
            reverse(
                f"admin:{self.opts.app_label}_{self.opts.model_name}_register_from_odoo"
            )
        )

    def _build_register_context(self, request):
        opts = self.model._meta
        context = self.admin_site.each_context(request)
        context.update(
            {
                "opts": opts,
                "title": _("Register from Odoo"),
                "has_credentials": False,
                "profile_url": None,
                "products": [],
                "selected_product_id": request.POST.get("product_id", ""),
            }
        )

        profile_admin = self._odoo_employee_admin()
        if profile_admin is not None:
            context["profile_url"] = profile_admin.get_my_profile_url(request)

        profile = getattr(request.user, "odoo_employee", None)
        if not profile or not profile.is_verified:
            context["credential_error"] = _(
                "Configure your Odoo employee before registering products."
            )
            return context, None

        try:
            products = profile.execute(
                "product.product",
                "search_read",
                fields=[
                    "name",
                    "description_sale",
                    "list_price",
                    "standard_price",
                ],
                limit=0,
            )
        except Exception as exc:
            logger.exception(
                "Failed to fetch Odoo products for user %s (profile_id=%s, host=%s, database=%s)",
                getattr(getattr(request, "user", None), "pk", None),
                getattr(profile, "pk", None),
                getattr(profile, "host", None),
                getattr(profile, "database", None),
            )
            context["error"] = _("Unable to fetch products from Odoo.")
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
            return context, []

        context["has_credentials"] = True
        simplified = []
        for product in products:
            simplified.append(
                {
                    "id": product.get("id"),
                    "name": product.get("name", ""),
                    "description_sale": product.get("description_sale", ""),
                    "list_price": product.get("list_price"),
                    "standard_price": product.get("standard_price"),
                }
            )
        context["products"] = simplified
        return context, simplified

    def register_from_odoo_view(self, request):
        context, products = self._build_register_context(request)
        if products is None:
            return TemplateResponse(
                request, "admin/core/product/register_from_odoo.html", context
            )

        if request.method == "POST" and context.get("has_credentials"):
            if not self.has_add_permission(request):
                context["form_error"] = _(
                    "You do not have permission to add products."
                )
            else:
                product_id = request.POST.get("product_id")
                if not product_id:
                    context["form_error"] = _("Select a product to register.")
                else:
                    try:
                        odoo_id = int(product_id)
                    except (TypeError, ValueError):
                        context["form_error"] = _("Invalid product selection.")
                    else:
                        match = next(
                            (item for item in products if item.get("id") == odoo_id),
                            None,
                        )
                        if not match:
                            context["form_error"] = _(
                                "The selected product was not found. Reload the page and try again."
                            )
                        else:
                            existing = self.model.objects.filter(
                                odoo_product__id=odoo_id
                            ).first()
                            if existing:
                                self.message_user(
                                    request,
                                    _(
                                        "Product %(name)s already imported; opening existing record."
                                    )
                                    % {"name": existing.name},
                                    level=messages.WARNING,
                                )
                                return HttpResponseRedirect(
                                    reverse(
                                        "admin:%s_%s_change"
                                        % (
                                            existing._meta.app_label,
                                            existing._meta.model_name,
                                        ),
                                        args=[existing.pk],
                                    )
                                )
                            product = self.model.objects.create(
                                name=match.get("name") or f"Odoo Product {odoo_id}",
                                description=match.get("description_sale", "") or "",
                                renewal_period=30,
                                odoo_product={
                                    "id": odoo_id,
                                    "name": match.get("name", ""),
                                },
                            )
                            self.log_addition(
                                request, product, "Registered product from Odoo"
                            )
                            self.message_user(
                                request,
                                _("Imported %(name)s from Odoo.")
                                % {"name": product.name},
                            )
                            return HttpResponseRedirect(
                                reverse(
                                    "admin:%s_%s_change"
                                    % (
                                        product._meta.app_label,
                                        product._meta.model_name,
                                    ),
                                    args=[product.pk],
                                )
                            )

        return TemplateResponse(
            request, "admin/core/product/register_from_odoo.html", context
        )


class RFIDImportForm(ImportForm):
    account_field = forms.ChoiceField(
        choices=(
            ("id", _("Energy account IDs")),
            ("name", _("Energy account names")),
        ),
        initial="id",
        label=_("Energy accounts"),
        required=False,
    )

    field_order = ["resource", "import_file", "format", "account_field"]

    def __init__(self, formats, resources, **kwargs):
        super().__init__(formats, resources, **kwargs)
        self.fields["account_field"].initial = (
            self.data.get("account_field")
            if hasattr(self, "data") and self.data
            else "id"
        )


class RFIDExportForm(SelectableFieldsExportForm):
    account_field = forms.ChoiceField(
        choices=(
            ("id", _("Energy account IDs")),
            ("name", _("Energy account names")),
        ),
        initial="id",
        label=_("Energy accounts"),
        required=False,
    )

    field_order = ["resource", "format", "account_field"]

    def __init__(self, formats, resources, **kwargs):
        super().__init__(formats, resources, **kwargs)
        if hasattr(self, "data") and self.data:
            self.fields["account_field"].initial = self.data.get("account_field", "id")


class RFIDConfirmImportForm(ConfirmImportForm):
    account_field = forms.CharField(widget=forms.HiddenInput(), required=False)

    def clean_account_field(self):
        value = (self.cleaned_data.get("account_field") or "id").lower()
        if value not in {"id", "name"}:
            return "id"
        return value


class RFIDResource(resources.ModelResource):
    energy_accounts = fields.Field(column_name="energy_accounts", readonly=True)
    reference = fields.Field(
        column_name="reference",
        attribute="reference",
        widget=ForeignKeyWidget(Reference, "value"),
    )

    def __init__(self, *args, account_field: str = "id", **kwargs):
        super().__init__(*args, **kwargs)
        self.account_field = account_field
        account_column = account_column_for_field(account_field)
        self.fields["energy_accounts"].column_name = account_column

    def get_instance(self, instance_loader, row):
        instance = super().get_instance(instance_loader, row)
        if instance is not None:
            return instance

        rfid_field = self.fields.get("rfid")
        if rfid_field is None:
            return None

        raw_value = row.get(rfid_field.column_name)
        normalized = RFID.normalize_code(str(raw_value or ""))
        if not normalized:
            return None

        existing = RFID.find_match(normalized)
        if existing is None:
            return None

        label_field = self.fields.get("label_id")
        if label_field is not None:
            row[label_field.column_name] = str(existing.pk)

        row[rfid_field.column_name] = normalized
        return existing

    def get_queryset(self):
        manager = getattr(self._meta.model, "all_objects", None)
        if manager is not None:
            return manager.all()
        return super().get_queryset()

    def dehydrate_energy_accounts(self, obj):
        return serialize_accounts(obj, self.account_field)

    def after_save_instance(self, instance, row, **kwargs):
        super().after_save_instance(instance, row, **kwargs)
        if kwargs.get("dry_run"):
            return
        accounts = parse_accounts(row, self.account_field)
        if accounts:
            instance.energy_accounts.set(accounts)
        else:
            instance.energy_accounts.clear()

    def before_save_instance(self, instance, row, **kwargs):
        if getattr(instance, "is_deleted", False):
            instance.is_deleted = False
        super().before_save_instance(instance, row, **kwargs)

    class Meta:
        model = RFID
        fields = (
            "label_id",
            "rfid",
            "custom_label",
            "energy_accounts",
            "reference",
            "external_command",
            "post_auth_command",
            "allowed",
            "color",
            "endianness",
            "kind",
            "released",
            "expiry_date",
            "last_seen_on",
        )
        export_order = (
            "label_id",
            "rfid",
            "custom_label",
            "energy_accounts",
            "reference",
            "external_command",
            "post_auth_command",
            "allowed",
            "color",
            "endianness",
            "kind",
            "released",
            "expiry_date",
            "last_seen_on",
        )
        import_id_fields = ("label_id",)


class RFIDForm(forms.ModelForm):
    """RFID admin form with optional reference field."""

    class Meta:
        model = RFID
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reference"].required = False
        rel = RFID._meta.get_field("reference").remote_field
        rel.model = ExperienceReference
        widget = self.fields["reference"].widget
        self.fields["reference"].widget = RelatedFieldWidgetWrapper(
            widget,
            rel,
            admin.site,
            can_add_related=True,
            can_change_related=True,
            can_view_related=True,
        )
        self.fields["data"].widget = RFIDDataWidget()


class CopyRFIDForm(forms.Form):
    """Simple form to capture the new RFID value when copying a tag."""

    rfid = forms.CharField(
        label=_("New RFID value"),
        max_length=RFID._meta.get_field("rfid").max_length,
        help_text=_("Enter the hexadecimal value for the new card."),
    )

    def clean_rfid(self):
        value = (self.cleaned_data.get("rfid") or "").strip()
        field = RFID._meta.get_field("rfid")
        try:
            cleaned = field.clean(value, None)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages)
        normalized = (cleaned or "").strip().upper()
        if not normalized:
            raise forms.ValidationError(_("RFID value is required."))
        if RFID.matching_queryset(normalized).exists():
            raise forms.ValidationError(
                _("An RFID with this value already exists.")
            )
        return normalized


class RFIDAdmin(EntityModelAdmin, ImportExportModelAdmin):
    change_list_template = "admin/cards/rfid/change_list.html"
    resource_class = RFIDResource
    import_form_class = RFIDImportForm
    confirm_form_class = RFIDConfirmImportForm
    export_form_class = RFIDExportForm
    list_display = (
        "label",
        "rfid",
        "color",
        "endianness_short",
        "released",
        "allowed",
        "last_seen_on",
    )
    list_filter = ("color", "endianness", "released", "allowed")
    search_fields = ("label_id", "rfid", "custom_label")
    autocomplete_fields = ["energy_accounts"]
    raw_id_fields = ["reference"]
    actions = [
        "scan_rfids",
        "print_card_labels",
        "print_release_form",
        "copy_rfids",
        "merge_rfids",
        "toggle_selected_released",
        "toggle_selected_allowed",
        "create_account_from_rfid",
    ]
    readonly_fields = ("added_on", "last_seen_on", "reversed_uid", "qr_test_link")
    form = RFIDForm

    def get_import_resource_kwargs(self, request, form=None, **kwargs):
        resource_kwargs = super().get_import_resource_kwargs(
            request, form=form, **kwargs
        )
        account_field = "id"
        if form and hasattr(form, "cleaned_data"):
            account_field = form.cleaned_data.get("account_field") or "id"
        resource_kwargs["account_field"] = (
            "name" if account_field == "name" else "id"
        )
        return resource_kwargs

    def get_confirm_form_initial(self, request, import_form):
        initial = super().get_confirm_form_initial(request, import_form)
        if import_form and hasattr(import_form, "cleaned_data"):
            initial["account_field"] = (
                import_form.cleaned_data.get("account_field") or "id"
            )
        return initial

    def get_export_resource_kwargs(self, request, **kwargs):
        export_form = kwargs.get("export_form")
        resource_kwargs = super().get_export_resource_kwargs(request, **kwargs)
        account_field = "id"
        if export_form and hasattr(export_form, "cleaned_data"):
            account_field = (
                export_form.cleaned_data.get("account_field") or "id"
            )
        resource_kwargs["account_field"] = (
            "name" if account_field == "name" else "id"
        )
        return resource_kwargs

    def label(self, obj):
        return obj.label_id

    label.admin_order_field = "label_id"
    label.short_description = "Label"

    @admin.display(description=_("End"), ordering="endianness")
    def endianness_short(self, obj):
        labels = {
            RFID.BIG_ENDIAN: _("Big"),
            RFID.LITTLE_ENDIAN: _("Little"),
        }
        return labels.get(obj.endianness, obj.get_endianness_display())

    def scan_rfids(self, request, queryset):
        return redirect("admin:cards_rfid_scan")

    scan_rfids.short_description = "Scan RFIDs"

    @staticmethod
    def _build_unique_account_name(base: str) -> str:
        base_name = (base or "").strip().upper() or "RFID ACCOUNT"
        candidate = base_name
        suffix = 1
        while CustomerAccount.objects.filter(name=candidate).exists():
            suffix += 1
            candidate = f"{base_name}-{suffix}"
        return candidate

    @admin.action(description=_("Create Account from RFID"))
    def create_account_from_rfid(self, request, queryset):
        created = 0
        reassigned = 0
        skipped = 0

        for tag in queryset.select_related():
            if tag.energy_accounts.exists():
                skipped += 1
                continue

            account_name = self._build_unique_account_name(
                tag.custom_label or tag.rfid
            )
            with transaction.atomic():
                account = CustomerAccount.objects.create(name=account_name)
                account.rfids.add(tag)

                updated = Transaction.objects.filter(
                    rfid__iexact=tag.rfid, account__isnull=True
                ).update(account=account)
                reassigned += updated

            created += 1

        if created:
            self.message_user(
                request,
                ngettext(
                    "Created %(count)d account from RFID selection.",
                    "Created %(count)d accounts from RFID selection.",
                    created,
                )
                % {"count": created},
                level=messages.SUCCESS,
            )

        if reassigned:
            self.message_user(
                request,
                ngettext(
                    "Linked %(count)d past transaction to the new account.",
                    "Linked %(count)d past transactions to the new accounts.",
                    reassigned,
                )
                % {"count": reassigned},
                level=messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d RFID because it is already linked to an account.",
                    "Skipped %(count)d RFIDs because they are already linked to accounts.",
                    skipped,
                )
                % {"count": skipped},
                level=messages.WARNING,
            )

    @admin.action(description=_("Toggle Released flag"))
    def toggle_selected_released(self, request, queryset):
        manager = getattr(self.model, "all_objects", self.model.objects)
        toggled = 0
        for tag in queryset:
            new_state = not tag.released
            manager.filter(pk=tag.pk).update(released=new_state)
            tag.released = new_state
            toggled += 1

        if toggled:
            self.message_user(
                request,
                ngettext(
                    "Toggled released flag for %(count)d RFID.",
                    "Toggled released flag for %(count)d RFIDs.",
                    toggled,
                )
                % {"count": toggled},
                level=messages.SUCCESS,
            )

    @admin.action(description=_("Toggle Allowed flag"))
    def toggle_selected_allowed(self, request, queryset):
        manager = getattr(self.model, "all_objects", self.model.objects)
        toggled = 0
        for tag in queryset:
            new_state = not tag.allowed
            manager.filter(pk=tag.pk).update(allowed=new_state)
            tag.allowed = new_state
            toggled += 1

        if toggled:
            self.message_user(
                request,
                ngettext(
                    "Toggled allowed flag for %(count)d RFID.",
                    "Toggled allowed flag for %(count)d RFIDs.",
                    toggled,
                )
                % {"count": toggled},
                level=messages.SUCCESS,
            )

    @admin.action(description=_("Copy RFID"))
    def copy_rfids(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                _("Select exactly one RFID to copy."),
                level=messages.ERROR,
            )
            return None

        source = (
            queryset.select_related("reference")
            .prefetch_related("energy_accounts")
            .first()
        )
        if source is None:
            self.message_user(
                request,
                _("Unable to find the selected RFID."),
                level=messages.ERROR,
            )
            return None

        if "apply" in request.POST:
            form = CopyRFIDForm(request.POST)
            if form.is_valid():
                new_rfid = form.cleaned_data["rfid"]
                label_id = RFID.next_copy_label(source)
                data_value = source.data or []
                copied_data = (
                    json.loads(json.dumps(data_value)) if data_value else []
                )
                create_kwargs = {
                    "label_id": label_id,
                    "rfid": new_rfid,
                    "custom_label": source.custom_label,
                    "key_a": source.key_a,
                    "key_b": source.key_b,
                    "key_a_verified": source.key_a_verified,
                    "key_b_verified": source.key_b_verified,
                    "allowed": source.allowed,
                    "external_command": source.external_command,
                    "post_auth_command": source.post_auth_command,
                    "color": source.color,
                    "kind": source.kind,
                    "reference": source.reference,
                    "released": source.released,
                    "data": copied_data,
                }
                try:
                    with transaction.atomic():
                        new_tag = RFID.objects.create(**create_kwargs)
                except IntegrityError:
                    form.add_error(
                        None, _("Unable to copy RFID. Please try again.")
                    )
                else:
                    new_tag.energy_accounts.set(source.energy_accounts.all())
                    self.message_user(
                        request,
                        _(
                            "Copied RFID %(source_label)s to %(new_label)s "
                            "(%(rfid)s)."
                        )
                        % {
                            "source_label": source.label_id,
                            "new_label": new_tag.label_id,
                            "rfid": new_tag.rfid,
                        },
                        level=messages.SUCCESS,
                    )
                    return HttpResponseRedirect(
                        reverse("admin:cards_rfid_change", args=[new_tag.pk])
                    )
        else:
            form = CopyRFIDForm()

        context = self.admin_site.each_context(request)
        context.update(
            {
                "opts": self.model._meta,
                "form": form,
                "source": source,
                "action": "copy_rfids",
                "title": _("Copy RFID"),
            }
        )
        context["media"] = self.media + form.media
        return TemplateResponse(request, "admin/cards/rfid/copy.html", context)

    @admin.action(description=_("Merge RFID cards"))
    def merge_rfids(self, request, queryset):
        tags = list(queryset.prefetch_related("energy_accounts"))
        if len(tags) < 2:
            self.message_user(
                request,
                _("Select at least two RFIDs to merge."),
                level=messages.WARNING,
            )
            return None

        normalized_map: dict[int, str] = {}
        groups: defaultdict[str, list[RFID]] = defaultdict(list)
        unmatched = 0
        for tag in tags:
            normalized = RFID.normalize_code(tag.rfid)
            normalized_map[tag.pk] = normalized
            if not normalized:
                unmatched += 1
                continue
            prefix = normalized[: RFID.MATCH_PREFIX_LENGTH]
            groups[prefix].append(tag)

        merge_groups: list[list[RFID]] = []
        skipped = unmatched
        for prefix, group in groups.items():
            if len(group) < 2:
                skipped += len(group)
                continue
            group.sort(
                key=lambda item: (
                    len(normalized_map.get(item.pk, "")),
                    normalized_map.get(item.pk, ""),
                    item.pk,
                )
            )
            merge_groups.append(group)

        if not merge_groups:
            self.message_user(
                request,
                _("No matching RFIDs were found to merge."),
                level=messages.WARNING,
            )
            return None

        merged_tags = 0
        merged_groups = 0
        conflicting_accounts = 0
        with transaction.atomic():
            for group in merge_groups:
                canonical = group[0]
                update_fields: set[str] = set()
                existing_account_ids = set(
                    canonical.energy_accounts.values_list("pk", flat=True)
                )
                for tag in group[1:]:
                    other_value = normalized_map.get(tag.pk, "")
                    if canonical.adopt_rfid(other_value):
                        update_fields.add("rfid")
                        normalized_map[canonical.pk] = RFID.normalize_code(
                            canonical.rfid
                        )
                    accounts = list(tag.energy_accounts.all())
                    if accounts:
                        transferable: list[CustomerAccount] = []
                        for account in accounts:
                            if existing_account_ids and account.pk not in existing_account_ids:
                                conflicting_accounts += 1
                                continue
                            transferable.append(account)
                        if transferable:
                            canonical.energy_accounts.add(*transferable)
                            existing_account_ids.update(
                                account.pk for account in transferable
                            )
                    if tag.allowed and not canonical.allowed:
                        canonical.allowed = True
                        update_fields.add("allowed")
                    if tag.released and not canonical.released:
                        canonical.released = True
                        update_fields.add("released")
                    if tag.key_a_verified and not canonical.key_a_verified:
                        canonical.key_a_verified = True
                        update_fields.add("key_a_verified")
                    if tag.key_b_verified and not canonical.key_b_verified:
                        canonical.key_b_verified = True
                        update_fields.add("key_b_verified")
                    if tag.last_seen_on and (
                        not canonical.last_seen_on
                        or tag.last_seen_on > canonical.last_seen_on
                    ):
                        canonical.last_seen_on = tag.last_seen_on
                        update_fields.add("last_seen_on")
                    if not canonical.origin_node and tag.origin_node_id:
                        canonical.origin_node = tag.origin_node
                        update_fields.add("origin_node")
                    merged_tags += 1
                    tag.delete()
                if update_fields:
                    canonical.save(update_fields=sorted(update_fields))
                merged_groups += 1

        if merged_tags:
            self.message_user(
                request,
                ngettext(
                    "Merged %(removed)d RFID into %(groups)d canonical record.",
                    "Merged %(removed)d RFIDs into %(groups)d canonical records.",
                    merged_tags,
                )
                % {"removed": merged_tags, "groups": merged_groups},
                level=messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d RFID because it did not share the first %(length)d characters with another selection.",
                    "Skipped %(count)d RFIDs because they did not share the first %(length)d characters with another selection.",
                    skipped,
                )
                % {"count": skipped, "length": RFID.MATCH_PREFIX_LENGTH},
                level=messages.WARNING,
            )

        if conflicting_accounts:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d customer account because the RFID was already linked to a different account.",
                    "Skipped %(count)d customer accounts because the RFID was already linked to a different account.",
                    conflicting_accounts,
                )
                % {"count": conflicting_accounts},
                level=messages.WARNING,
            )

    def _render_card_labels(
        self,
        request,
        queryset,
        empty_message,
        redirect_url,
    ):
        queryset = queryset.select_related("reference").order_by("label_id")
        if not queryset.exists():
            self.message_user(
                request,
                empty_message,
                level=messages.WARNING,
            )
            return HttpResponseRedirect(redirect_url)

        buffer = BytesIO()
        base_card_width = 85.6 * mm
        base_card_height = 54 * mm
        columns = 3
        rows = 4
        labels_per_page = columns * rows
        page_margin_x = 12 * mm
        page_margin_y = 12 * mm
        column_spacing = 6 * mm
        row_spacing = 6 * mm
        page_size = landscape(letter)
        page_width, page_height = page_size

        available_width = (
            page_width - (2 * page_margin_x) - (columns - 1) * column_spacing
        )
        available_height = (
            page_height - (2 * page_margin_y) - (rows - 1) * row_spacing
        )
        scale_x = available_width / (columns * base_card_width)
        scale_y = available_height / (rows * base_card_height)
        scale = min(scale_x, scale_y, 1)

        card_width = base_card_width * scale
        card_height = base_card_height * scale
        margin = 5 * mm * scale
        highlight_height = 20 * mm * scale
        content_width = card_width - 2 * margin
        left_section_width = content_width * 0.6
        right_section_width = content_width - left_section_width

        def draw_label(pdf_canvas, tag, origin_x, origin_y):
            pdf_canvas.saveState()
            pdf_canvas.translate(origin_x, origin_y)

            pdf_canvas.setFillColor(colors.white)
            pdf_canvas.rect(0, 0, card_width, card_height, stroke=0, fill=1)
            pdf_canvas.setStrokeColor(colors.HexColor("#D9D9D9"))
            pdf_canvas.setLineWidth(max(0.3, 0.5 * scale))
            pdf_canvas.rect(0, 0, card_width, card_height, stroke=1, fill=0)

            left_x = margin
            right_x = left_x + left_section_width
            highlight_bottom = card_height - margin - highlight_height

            pdf_canvas.setFillColor(colors.HexColor("#E6EEF8"))
            pdf_canvas.roundRect(
                left_x,
                highlight_bottom,
                left_section_width,
                highlight_height,
                6 * scale,
                stroke=0,
                fill=1,
            )

            pdf_canvas.setFillColor(colors.HexColor("#1A1A1A"))
            font_name = "Helvetica-Bold"
            font_size = max(6, 28 * scale)
            pdf_canvas.setFont(font_name, font_size)
            label_value = str(tag.label_id or "")
            primary_label = label_value.zfill(4) if label_value.isdigit() else label_value
            descent = abs(pdfmetrics.getDescent(font_name) / 1000 * font_size)
            vertical_center = highlight_bottom + (highlight_height / 2)
            baseline = vertical_center - (descent / 2)
            pdf_canvas.drawCentredString(
                left_x + (left_section_width / 2),
                baseline,
                primary_label,
            )

            pdf_canvas.setFont("Helvetica", max(5, 11 * scale))
            text = pdf_canvas.beginText()
            text.setTextOrigin(left_x, highlight_bottom - 16 * scale)
            text.setLeading(max(6, 14 * scale))

            details = [_("RFID: %s") % tag.rfid]
            if tag.custom_label:
                details.append(_("Custom label: %s") % tag.custom_label)
            details.append(_("Color: %s") % tag.get_color_display())
            details.append(_("Type: %s") % tag.get_kind_display())
            if tag.reference:
                details.append(_("Reference: %s") % tag.reference)

            for line in details:
                text.textLine(line)

            pdf_canvas.drawText(text)

            if tag.rfid:
                qr_code = qr.QrCodeWidget(str(tag.rfid))
                qr_bounds = qr_code.getBounds()
                qr_width = qr_bounds[2] - qr_bounds[0]
                qr_height = qr_bounds[3] - qr_bounds[1]
                qr_target_size = min(right_section_width, card_height - 2 * margin)
                if qr_width and qr_height:
                    qr_scale = qr_target_size / max(qr_width, qr_height)
                    drawing = Drawing(
                        qr_target_size,
                        qr_target_size,
                        transform=[qr_scale, 0, 0, qr_scale, 0, 0],
                    )
                    drawing.add(qr_code)
                    qr_x = right_x + (right_section_width - qr_target_size) / 2
                    qr_y = margin + (card_height - 2 * margin - qr_target_size) / 2
                    renderPDF.draw(drawing, pdf_canvas, qr_x, qr_y)

            pdf_canvas.restoreState()

        pdf = canvas.Canvas(buffer, pagesize=page_size)
        pdf.setTitle("RFID Card Labels")

        tags = list(queryset)
        total_tags = len(tags)

        for page_start in range(0, total_tags, labels_per_page):
            pdf.setPageSize(page_size)
            pdf.setFillColor(colors.white)
            pdf.rect(0, 0, page_width, page_height, stroke=0, fill=1)
            subset = tags[page_start : page_start + labels_per_page]

            for index, tag in enumerate(subset):
                column = index % columns
                row = index // columns
                x = page_margin_x + column * (card_width + column_spacing)
                y = (
                    page_height
                    - page_margin_y
                    - card_height
                    - row * (card_height + row_spacing)
                )
                draw_label(pdf, tag, x, y)

            pdf.showPage()

        pdf.save()
        buffer.seek(0)

        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = "attachment; filename=rfid-card-labels.pdf"
        return response

    def print_card_labels(self, request, queryset):
        return self._render_card_labels(
            request,
            queryset,
            _("Select at least one RFID to print labels."),
            request.get_full_path(),
        )

    print_card_labels.short_description = _("Print Card Labels")

    def _render_release_form(self, request, queryset, empty_message, redirect_url):
        tags = list(queryset)
        if not tags:
            self.message_user(request, empty_message, level=messages.WARNING)
            return HttpResponseRedirect(redirect_url)

        language = getattr(request, "LANGUAGE_CODE", translation.get_language())
        if not language:
            language = settings.LANGUAGE_CODE

        with translation.override(language):
            buffer = BytesIO()
            document = SimpleDocTemplate(
                buffer,
                pagesize=letter,
                leftMargin=36,
                rightMargin=36,
                topMargin=72,
                bottomMargin=36,
            )
            document.title = str(_("RFID Release Form"))

            styles = getSampleStyleSheet()
            story = []
            story.append(Paragraph(_("RFID Release Form"), styles["Title"]))
            story.append(Spacer(1, 12))

            generated_on = timezone.localtime()
            formatted_generated_on = date_format(generated_on, "DATETIME_FORMAT")
            if generated_on.tzinfo:
                formatted_generated_on = _("%(datetime)s %(timezone)s") % {
                    "datetime": formatted_generated_on,
                    "timezone": generated_on.tzname() or "",
                }
            generated_text = Paragraph(
                _("Generated on: %(date)s")
                % {"date": formatted_generated_on},
                styles["Normal"],
            )
            story.append(generated_text)
            story.append(Spacer(1, 24))

            table_data = [
                [
                    _("Label"),
                    _("RFID"),
                    _("Custom label"),
                    _("Color"),
                    _("Type"),
                ]
            ]

            for tag in tags:
                table_data.append(
                    [
                        tag.label_id or "",
                        tag.rfid or "",
                        tag.custom_label or "",
                        tag.get_color_display() if tag.color else "",
                        tag.get_kind_display() if tag.kind else "",
                    ]
                )

            table = Table(table_data, repeatRows=1, hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ]
                )
            )

            story.append(table)
            story.append(Spacer(1, 36))

            signature_lines = [
                [
                    Paragraph(
                        _("Issuer Signature: ______________________________"),
                        styles["Normal"],
                    ),
                    Paragraph(
                        _("Receiver Signature: ______________________________"),
                        styles["Normal"],
                    ),
                ],
                [
                    Paragraph(
                        _("Issuer Name: ______________________________"),
                        styles["Normal"],
                    ),
                    Paragraph(
                        _("Receiver Name: ______________________________"),
                        styles["Normal"],
                    ),
                ],
            ]

            signature_table = Table(
                signature_lines,
                colWidths=[document.width / 2.0, document.width / 2.0],
                hAlign="LEFT",
            )
            signature_table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                    ]
                )
            )
            story.append(signature_table)

            document.build(story)
            buffer.seek(0)

            response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
            response["Content-Disposition"] = "attachment; filename=rfid-release-form.pdf"
            return response

    def print_release_form(self, request, queryset):
        return self._render_release_form(
            request,
            queryset,
            _("Select at least one RFID to print the release form."),
            request.get_full_path(),
        )

    print_release_form.short_description = _("Print Release Form")

    def get_changelist_actions(self, request):
        parent = getattr(super(), "get_changelist_actions", None)
        actions = []
        if callable(parent):
            parent_actions = parent(request)
            if parent_actions:
                actions.extend(parent_actions)
        actions.append("print_valid_card_labels")
        return actions

    def print_valid_card_labels(self, request):
        queryset = self.get_queryset(request).filter(allowed=True, released=True)
        changelist_url = reverse("admin:cards_rfid_changelist")
        return self._render_card_labels(
            request,
            queryset,
            _("No RFID cards marked as valid are available to print."),
            changelist_url,
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "report/",
                self.admin_site.admin_view(self.report_view),
                name="cards_rfid_report",
            ),
            path(
                "print-valid-labels/",
                self.admin_site.admin_view(self.print_valid_card_labels),
                name="cards_rfid_print_valid_card_labels",
            ),
            path(
                "scan/",
                self.admin_site.admin_view(csrf_exempt(self.scan_view)),
                name="cards_rfid_scan",
            ),
            path(
                "scan/next/",
                self.admin_site.admin_view(csrf_exempt(self.scan_next)),
                name="cards_rfid_scan_next",
            ),
        ]
        return custom + urls

    def report_view(self, request):
        context = self.admin_site.each_context(request)
        context["report"] = ClientReport.build_rows(for_display=True)
        return TemplateResponse(request, "admin/cards/rfid/report.html", context)

    def scan_view(self, request):
        context = self.admin_site.each_context(request)
        table_mode, toggle_url, toggle_label = build_mode_toggle(request)
        public_view_url = reverse("rfid-reader")
        if table_mode:
            public_view_url = f"{public_view_url}?mode=table"
        context.update(
            {
                "scan_url": reverse("admin:cards_rfid_scan_next"),
                "admin_change_url_template": reverse(
                    "admin:cards_rfid_change", args=[0]
                ),
                "title": _("Scan RFIDs"),
                "opts": self.model._meta,
                "table_mode": table_mode,
                "toggle_url": toggle_url,
                "toggle_label": toggle_label,
                "public_view_url": public_view_url,
                "deep_read_url": reverse("rfid-scan-deep"),
            }
        )
        context["title"] = _("Scan RFIDs")
        context["opts"] = self.model._meta
        context["show_release_info"] = True
        context["default_endianness"] = RFID.BIG_ENDIAN
        return render(request, "admin/cards/rfid/scan.html", context)

    def scan_next(self, request):
        from apps.rfids.scanner import scan_sources
        from apps.rfids.reader import validate_rfid_value

        if request.method == "POST":
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return JsonResponse({"error": "Invalid JSON payload"}, status=400)
            rfid = payload.get("rfid") or payload.get("value")
            kind = payload.get("kind")
            endianness = payload.get("endianness")
            result = validate_rfid_value(rfid, kind=kind, endianness=endianness)
        else:
            endianness = request.GET.get("endianness")
            result = scan_sources(request, endianness=endianness)
        status = 500 if result.get("error") else 200
        return JsonResponse(result, status=status)



