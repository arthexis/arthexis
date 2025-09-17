from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.urls import path, reverse
from django.shortcuts import redirect, render
from django.http import JsonResponse, HttpResponseBase, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import (
    GroupAdmin as DjangoGroupAdmin,
    UserAdmin as DjangoUserAdmin,
)
from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget
from django.contrib.auth.models import Group
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.forms.models import BaseInlineFormSet
import json
import uuid
import requests
import datetime
import calendar
from django_object_actions import DjangoObjectActions
from ocpp.models import Transaction
from nodes.models import EmailOutbox
from .models import (
    User,
    UserPhoneNumber,
    EnergyAccount,
    ElectricVehicle,
    Brand,
    EVModel,
    WMICode,
    EnergyCredit,
    ClientReport,
    Product,
    LiveSubscription,
    RFID,
    SigilRoot,
    CustomSigil,
    Reference,
    OdooProfile,
    EmailInbox,
    EmailCollector,
    Package,
    PackageRelease,
    ReleaseManager,
    SecurityGroup,
    InviteLead,
    APLead,
    AssistantProfile,
    Todo,
    hash_key,
)
from .user_data import EntityModelAdmin, delete_user_fixture, dump_user_fixture
from .widgets import OdooProductWidget
from .mcp import process as mcp_process


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


class ExperienceReference(Reference):
    class Meta:
        proxy = True
        app_label = "pages"
        verbose_name = Reference._meta.verbose_name
        verbose_name_plural = Reference._meta.verbose_name_plural


class CustomSigilAdminForm(forms.ModelForm):
    class Meta:
        model = CustomSigil
        fields = ["prefix", "content_type"]


@admin.register(CustomSigil)
class CustomSigilAdmin(EntityModelAdmin):
    form = CustomSigilAdminForm
    list_display = ("prefix", "content_type")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(context_type=SigilRoot.Context.ENTITY)

    def save_model(self, request, obj, form, change):
        obj.context_type = SigilRoot.Context.ENTITY
        super().save_model(request, obj, form, change)


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


@admin.register(ExperienceReference)
class ReferenceAdmin(EntityModelAdmin):
    list_display = (
        "alt_text",
        "content_type",
        "include_in_footer",
        "footer_visibility",
        "author",
        "transaction_uuid",
    )
    readonly_fields = ("uses", "qr_code", "author")
    fields = (
        "alt_text",
        "content_type",
        "value",
        "file",
        "method",
        "include_in_footer",
        "footer_visibility",
        "transaction_uuid",
        "author",
        "uses",
        "qr_code",
    )

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj:
            ro.append("transaction_uuid")
        return ro

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "bulk/",
                self.admin_site.admin_view(csrf_exempt(self.bulk_create)),
                name="core_reference_bulk",
            ),
        ]
        return custom + urls

    def bulk_create(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "POST required"}, status=405)
        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        refs = payload.get("references", [])
        transaction_uuid = payload.get("transaction_uuid") or uuid.uuid4()
        created_ids = []
        for data in refs:
            ref = Reference.objects.create(
                alt_text=data.get("alt_text", ""),
                value=data.get("value", ""),
                transaction_uuid=transaction_uuid,
                author=request.user if request.user.is_authenticated else None,
            )
            created_ids.append(ref.id)
        return JsonResponse(
            {"transaction_uuid": str(transaction_uuid), "ids": created_ids}
        )

    def qr_code(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" alt="{}" style="height:200px;"/>',
                obj.image.url,
                obj.alt_text,
            )
        return ""

    qr_code.short_description = "QR Code"


class ReleaseManagerAdminForm(forms.ModelForm):
    class Meta:
        model = ReleaseManager
        fields = "__all__"
        widgets = {
            "pypi_token": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
            "github_token": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
        }


@admin.register(ReleaseManager)
class ReleaseManagerAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    form = ReleaseManagerAdminForm
    list_display = ("owner", "pypi_username", "pypi_url")
    actions = ["test_credentials"]
    change_actions = ["test_credentials_action"]
    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        (
            "Credentials",
            {
                "fields": (
                    "pypi_username",
                    "pypi_token",
                    "pypi_password",
                    "github_token",
                    "pypi_url",
                )
            },
        ),
    )

    def owner(self, obj):
        return obj.owner_display()

    owner.short_description = "Owner"

    @admin.action(description="Test credentials")
    def test_credentials(self, request, queryset):
        for manager in queryset:
            self._test_credentials(request, manager)

    def test_credentials_action(self, request, obj):
        self._test_credentials(request, obj)

    test_credentials_action.label = "Test credentials"
    test_credentials_action.short_description = "Test credentials"

    def _test_credentials(self, request, manager):
        creds = manager.to_credentials()
        if not creds:
            self.message_user(request, f"{manager} has no credentials", messages.ERROR)
            return
        url = manager.pypi_url or "https://upload.pypi.org/legacy/"
        auth = (
            ("__token__", creds.token)
            if creds.token
            else (creds.username, creds.password)
        )
        try:
            resp = requests.get(url, auth=auth, timeout=10)
            if resp.ok:
                self.message_user(
                    request, f"{manager} credentials valid", messages.SUCCESS
                )
            else:
                self.message_user(
                    request,
                    f"{manager} credentials invalid ({resp.status_code})",
                    messages.ERROR,
                )
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request, f"{manager} credentials check failed: {exc}", messages.ERROR
            )

    def get_model_perms(self, request):
        return {}


@admin.register(Package)
class PackageAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    list_display = (
        "name",
        "description",
        "homepage_url",
        "release_manager",
        "is_active",
    )
    actions = ["prepare_next_release"]
    change_actions = ["prepare_next_release_action"]

    def _prepare(self, request, package):
        from pathlib import Path
        from packaging.version import Version

        ver_file = Path("VERSION")
        repo_version = (
            Version(ver_file.read_text().strip())
            if ver_file.exists()
            else Version("0.0.0")
        )

        pypi_latest = Version("0.0.0")
        try:
            resp = requests.get(
                f"https://pypi.org/pypi/{package.name}/json", timeout=10
            )
            if resp.ok:
                releases = resp.json().get("releases", {})
                if releases:
                    pypi_latest = max(Version(v) for v in releases)
        except Exception:
            pass
        pypi_plus_one = Version(
            f"{pypi_latest.major}.{pypi_latest.minor}.{pypi_latest.micro + 1}"
        )
        next_version = max(repo_version, pypi_plus_one)
        release, _created = PackageRelease.all_objects.update_or_create(
            package=package,
            version=str(next_version),
            defaults={
                "release_manager": package.release_manager,
                "is_deleted": False,
            },
        )
        return redirect(reverse("admin:core_packagerelease_change", args=[release.pk]))

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "prepare-next-release/",
                self.admin_site.admin_view(self.prepare_next_release_active),
                name="core_package_prepare_next_release",
            )
        ]
        return custom + urls

    def prepare_next_release_active(self, request):
        package = Package.objects.filter(is_active=True).first()
        if not package:
            self.message_user(request, "No active package", messages.ERROR)
            return redirect("admin:core_package_changelist")
        return self._prepare(request, package)

    @admin.action(description="Prepare next Release")
    def prepare_next_release(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one package", messages.ERROR)
            return
        return self._prepare(request, queryset.first())

    def prepare_next_release_action(self, request, obj):
        return self._prepare(request, obj)

    prepare_next_release_action.label = "Prepare next Release"
    prepare_next_release_action.short_description = "Prepare next release"


class SecurityGroupAdminForm(forms.ModelForm):
    users = forms.ModelMultipleChoiceField(
        queryset=get_user_model().objects.all(),
        required=False,
        widget=admin.widgets.FilteredSelectMultiple("users", False),
    )

    class Meta:
        model = SecurityGroup
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["users"].initial = self.instance.user_set.all()

    def save(self, commit=True):
        instance = super().save(commit)
        users = self.cleaned_data.get("users")
        if commit:
            instance.user_set.set(users)
        else:
            self.save_m2m = lambda: instance.user_set.set(users)
        return instance


class SecurityGroupAdmin(DjangoGroupAdmin):
    form = SecurityGroupAdminForm
    fieldsets = ((None, {"fields": ("name", "parent", "users", "permissions")}),)
    filter_horizontal = ("permissions",)


class InviteLeadAdmin(EntityModelAdmin):
    list_display = ("email", "created_on", "sent_on", "short_error")
    search_fields = ("email", "comment")
    readonly_fields = (
        "created_on",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "sent_on",
        "error",
    )

    def short_error(self, obj):
        return (obj.error[:40] + "…") if len(obj.error) > 40 else obj.error

    short_error.short_description = "error"


@admin.register(APLead)
class APLeadAdmin(EntityModelAdmin):
    list_display = ("ssid", "mode", "created_on", "user")
    list_filter = ("mode", "created_on")
    search_fields = ("ssid", "notes", "user__username")
    readonly_fields = (
        "created_on",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
    )


class EnergyAccountRFIDForm(forms.ModelForm):
    """Form for assigning existing RFIDs to an energy account."""

    class Meta:
        model = EnergyAccount.rfids.through
        fields = ["rfid"]

    def clean_rfid(self):
        rfid = self.cleaned_data["rfid"]
        if rfid.energy_accounts.exclude(pk=self.instance.energyaccount_id).exists():
            raise forms.ValidationError(
                "RFID is already assigned to another energy account"
            )
        return rfid


class EnergyAccountRFIDInline(admin.TabularInline):
    model = EnergyAccount.rfids.through
    form = EnergyAccountRFIDForm
    autocomplete_fields = ["rfid"]
    extra = 0
    verbose_name = "RFID"
    verbose_name_plural = "RFIDs"


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


def _restore_sigil_values(form, field_names):
    """Reset sigil fields on ``form.instance`` to their raw form values."""

    for name in field_names:
        if name not in form.fields:
            continue
        if name in form.cleaned_data:
            raw = form.cleaned_data[name]
        else:
            raw = _raw_instance_value(form.instance, name)
        setattr(form.instance, name, raw)


class OdooProfileAdminForm(forms.ModelForm):
    """Admin form for :class:`core.models.OdooProfile` with hidden password."""

    password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave blank to keep the current password.",
    )

    class Meta:
        model = OdooProfile
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
            return _raw_instance_value(self.instance, "password")
        return pwd

    def _post_clean(self):
        super()._post_clean()
        _restore_sigil_values(
            self,
            ["host", "database", "username", "password"],
        )


class EmailInboxAdminForm(forms.ModelForm):
    """Admin form for :class:`core.models.EmailInbox` with hidden password."""

    password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave blank to keep the current password.",
    )

    class Meta:
        model = EmailInbox
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
            return _raw_instance_value(self.instance, "password")
        return pwd

    def _post_clean(self):
        super()._post_clean()
        _restore_sigil_values(
            self,
            ["username", "host", "password", "protocol"],
        )


class ProfileInlineFormSet(BaseInlineFormSet):
    """Hide deletion controls and allow implicit removal when empty."""

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


class OdooProfileInlineForm(ProfileFormMixin, OdooProfileAdminForm):
    profile_fields = OdooProfile.profile_fields

    class Meta(OdooProfileAdminForm.Meta):
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


class EmailOutboxInlineForm(ProfileFormMixin, forms.ModelForm):
    profile_fields = EmailOutbox.profile_fields
    password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave blank to keep the current password.",
    )

    class Meta:
        model = EmailOutbox
        fields = (
            "password",
            "host",
            "port",
            "username",
            "use_tls",
            "use_ssl",
            "from_email",
        )

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
            return _raw_instance_value(self.instance, "password")
        return pwd

    def _post_clean(self):
        super()._post_clean()
        _restore_sigil_values(
            self,
            ["password", "host", "username", "from_email"],
        )


class ReleaseManagerInlineForm(ProfileFormMixin, forms.ModelForm):
    profile_fields = ReleaseManager.profile_fields

    class Meta:
        model = ReleaseManager
        fields = (
            "pypi_username",
            "pypi_token",
            "github_token",
            "pypi_password",
            "pypi_url",
        )
        widgets = {
            "pypi_token": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
            "github_token": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
        }


class AssistantProfileInlineForm(ProfileFormMixin, forms.ModelForm):
    user_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True),
        help_text="Provide a plain key to create or rotate credentials.",
    )
    profile_fields = ("user_key", "scopes", "is_active")

    class Meta:
        model = AssistantProfile
        fields = ("scopes", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and "is_active" in self.fields:
            self.fields["is_active"].initial = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE"):
            return cleaned
        if not self.instance.pk and not cleaned.get("user_key"):
            if cleaned.get("scopes") or cleaned.get("is_active"):
                raise forms.ValidationError(
                    "Provide a user key to create an assistant profile."
                )
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        user_key = self.cleaned_data.get("user_key")
        if user_key:
            instance.user_key_hash = hash_key(user_key)
            instance.last_used_at = None
        if commit:
            instance.save()
            self.save_m2m()
        return instance


PROFILE_INLINE_CONFIG = {
    OdooProfile: {
        "form": OdooProfileInlineForm,
        "fieldsets": (
            (
                None,
                {
                    "fields": (
                        "user_datum",
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
            "user_datum",
            "username",
            "host",
            "port",
            "password",
            "protocol",
            "use_ssl",
        ),
    },
    EmailOutbox: {
        "form": EmailOutboxInlineForm,
        "fields": (
            "user_datum",
            "password",
            "host",
            "port",
            "username",
            "use_tls",
            "use_ssl",
            "from_email",
        ),
    },
    ReleaseManager: {
        "form": ReleaseManagerInlineForm,
        "fields": (
            "user_datum",
            "pypi_username",
            "pypi_token",
            "github_token",
            "pypi_password",
            "pypi_url",
        ),
    },
    AssistantProfile: {
        "form": AssistantProfileInlineForm,
        "fields": ("user_datum", "user_key", "scopes", "is_active"),
        "readonly_fields": ("user_key_hash", "created_at", "last_used_at"),
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
    }
    if "fieldsets" in config:
        attrs["fieldsets"] = config["fieldsets"]
    if "fields" in config:
        attrs["fields"] = config["fields"]
    if "readonly_fields" in config:
        attrs["readonly_fields"] = config["readonly_fields"]
    return type(
        f"{model.__name__}{owner_field.title()}Inline",
        (admin.StackedInline,),
        attrs,
    )


PROFILE_MODELS = (
    OdooProfile,
    EmailInbox,
    EmailOutbox,
    ReleaseManager,
    AssistantProfile,
)
USER_PROFILE_INLINES = [
    _build_profile_inline(model, "user") for model in PROFILE_MODELS
]
GROUP_PROFILE_INLINES = [
    _build_profile_inline(model, "group") for model in PROFILE_MODELS
]

SecurityGroupAdmin.inlines = GROUP_PROFILE_INLINES


class UserPhoneNumberInline(admin.TabularInline):
    model = UserPhoneNumber
    extra = 0
    fields = ("number", "priority")


class UserAdmin(DjangoUserAdmin):
    fieldsets = _append_operate_as(DjangoUserAdmin.fieldsets)
    add_fieldsets = _append_operate_as(DjangoUserAdmin.add_fieldsets)
    inlines = USER_PROFILE_INLINES + [UserPhoneNumberInline]
    change_form_template = "admin/user_profile_change_form.html"

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        context = super().render_change_form(
            request, context, add=add, change=change, form_url=form_url, obj=obj
        )
        context["show_user_datum"] = False
        context["show_seed_datum"] = False
        return context

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


class EmailCollectorInline(admin.TabularInline):
    model = EmailCollector
    extra = 0


class EmailCollectorAdmin(EntityModelAdmin):
    list_display = ("inbox", "subject", "sender", "body", "fragment")
    search_fields = ("subject", "sender", "body", "fragment")


@admin.register(OdooProfile)
class OdooProfileAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    change_form_template = "django_object_actions/change_form.html"
    form = OdooProfileAdminForm
    list_display = ("owner", "host", "database", "verified_on")
    readonly_fields = ("verified_on", "odoo_uid", "name", "email")
    actions = ["verify_credentials"]
    change_actions = ["verify_credentials_action"]
    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        (None, {"fields": ("host", "database", "username", "password")}),
        (
            "Odoo Employee",
            {"fields": ("verified_on", "odoo_uid", "name", "email")},
        ),
    )

    def owner(self, obj):
        return obj.owner_display()

    owner.short_description = "Owner"

    def _verify_credentials(self, request, profile):
        try:
            profile.verify()
            self.message_user(request, f"{profile.owner_display()} verified")
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request, f"{profile.owner_display()}: {exc}", level=messages.ERROR
            )

    @admin.action(description="Test credentials")
    def verify_credentials(self, request, queryset):
        for profile in queryset:
            self._verify_credentials(request, profile)

    def verify_credentials_action(self, request, obj):
        self._verify_credentials(request, obj)

    verify_credentials_action.label = "Test credentials"
    verify_credentials_action.short_description = "Test credentials"

    def get_model_perms(self, request):
        return {}


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


class EmailInboxAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    form = EmailInboxAdminForm
    list_display = ("owner_label", "username", "host", "protocol")
    actions = ["test_connection", "search_inbox", "test_collectors"]
    change_actions = ["test_collectors_action"]
    change_form_template = "admin/core/emailinbox/change_form.html"
    inlines = [EmailCollectorInline]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/test/",
                self.admin_site.admin_view(self.test_inbox),
                name="core_emailinbox_test",
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
                "admin:core_emailinbox_test", args=[object_id]
            )
        return super().changeform_view(request, object_id, form_url, extra_context)

    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        (
            None,
            {
                "fields": (
                    "username",
                    "host",
                    "port",
                    "password",
                    "protocol",
                    "use_ssl",
                )
            },
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


@admin.register(AssistantProfile)
class AssistantProfileAdmin(EntityModelAdmin):
    list_display = ("owner", "created_at", "last_used_at", "is_active")
    readonly_fields = ("user_key_hash", "created_at", "last_used_at")

    change_form_template = "admin/workgroupassistantprofile_change_form.html"
    change_list_template = "admin/assistantprofile_change_list.html"
    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        (
            None,
            {
                "fields": (
                    "scopes",
                    "is_active",
                    "user_key_hash",
                    "created_at",
                    "last_used_at",
                )
            },
        ),
    )

    def owner(self, obj):
        return obj.owner_display()

    owner.short_description = "Owner"

    def get_urls(self):
        urls = super().get_urls()
        opts = self.model._meta
        app_label = opts.app_label
        model_name = opts.model_name
        custom = [
            path(
                "<path:object_id>/generate-key/",
                self.admin_site.admin_view(self.generate_key),
                name=f"{app_label}_{model_name}_generate_key",
            ),
            path(
                "server/start/",
                self.admin_site.admin_view(self.start_server),
                name=f"{app_label}_{model_name}_start_server",
            ),
            path(
                "server/stop/",
                self.admin_site.admin_view(self.stop_server),
                name=f"{app_label}_{model_name}_stop_server",
            ),
            path(
                "server/status/",
                self.admin_site.admin_view(self.server_status),
                name=f"{app_label}_{model_name}_status",
            ),
        ]
        return custom + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        status = mcp_process.get_status()
        opts = self.model._meta
        app_label = opts.app_label
        model_name = opts.model_name
        extra_context.update(
            {
                "mcp_status": status,
                "mcp_server_actions": {
                    "start": reverse(
                        f"admin:{app_label}_{model_name}_start_server"
                    ),
                    "stop": reverse(
                        f"admin:{app_label}_{model_name}_stop_server"
                    ),
                    "status": reverse(
                        f"admin:{app_label}_{model_name}_status"
                    ),
                },
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

    def _redirect_to_changelist(self):
        opts = self.model._meta
        return HttpResponseRedirect(
            reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
        )

    def generate_key(self, request, object_id, *args, **kwargs):
        profile = self.get_object(request, object_id)
        if profile is None:
            return HttpResponseRedirect("../")
        if profile.user is None:
            self.message_user(
                request,
                "Assign a user before generating a key.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect("../")
        profile, key = AssistantProfile.issue_key(profile.user)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": profile,
            "user_key": key,
        }
        return TemplateResponse(request, "admin/assistantprofile_key.html", context)

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        response = super().render_change_form(
            request, context, add=add, change=change, form_url=form_url, obj=obj
        )
        config = dict(getattr(settings, "MCP_SIGIL_SERVER", {}))
        host = config.get("host") or "127.0.0.1"
        port = config.get("port", 8800)
        if isinstance(response, dict):
            response.setdefault("mcp_server_host", host)
            response.setdefault("mcp_server_port", port)
        else:
            context_data = getattr(response, "context_data", None)
            if context_data is not None:
                context_data.setdefault("mcp_server_host", host)
                context_data.setdefault("mcp_server_port", port)
        return response

    def start_server(self, request):
        try:
            pid = mcp_process.start_server()
        except mcp_process.ServerAlreadyRunningError as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
        except mcp_process.ServerStartError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.message_user(
                request,
                f"Started MCP server (PID {pid}).",
                level=messages.SUCCESS,
            )
        return self._redirect_to_changelist()

    def stop_server(self, request):
        try:
            pid = mcp_process.stop_server()
        except mcp_process.ServerNotRunningError as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
        except mcp_process.ServerStopError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.message_user(
                request,
                f"Stopped MCP server (PID {pid}).",
                level=messages.SUCCESS,
            )
        return self._redirect_to_changelist()

    def server_status(self, request):
        status = mcp_process.get_status()
        if status["running"]:
            msg = f"MCP server is running (PID {status['pid']})."
            level = messages.INFO
        else:
            msg = "MCP server is not running."
            level = messages.WARNING
        if status.get("last_error"):
            msg = f"{msg} {status['last_error']}"
        self.message_user(request, msg, level=level)
        return self._redirect_to_changelist()

    def get_model_perms(self, request):
        return {}


class EnergyCreditInline(admin.TabularInline):
    model = EnergyCredit
    fields = ("amount_kw", "created_by", "created_on")
    readonly_fields = ("created_by", "created_on")
    extra = 0


@admin.register(EnergyAccount)
class EnergyAccountAdmin(EntityModelAdmin):
    change_list_template = "admin/core/energyaccount/change_list.html"
    change_form_template = "admin/user_datum_change_form.html"
    list_display = (
        "name",
        "user",
        "credits_kw",
        "total_kw_spent",
        "balance_kw",
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
    inlines = [EnergyAccountRFIDInline, EnergyCreditInline]
    actions = ["test_authorization"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "user",
                    ("service_account", "authorized"),
                    ("credits_kw", "total_kw_spent", "balance_kw"),
                )
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

    # Onboarding wizard view
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "onboard/",
                self.admin_site.admin_view(self.onboard_details),
                name="core_energyaccount_onboard_details",
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
                account = EnergyAccount.objects.create(user=user, name=username.upper())
                rfid_val = form.cleaned_data["rfid"].upper()
                if rfid_val:
                    tag, _ = RFID.objects.get_or_create(rfid=rfid_val)
                    account.rfids.add(tag)
                vehicle_vin = form.cleaned_data["vehicle_id"]
                if vehicle_vin:
                    ElectricVehicle.objects.create(account=account, vin=vehicle_vin)
                self.message_user(request, "Customer onboarded")
                return redirect("admin:core_energyaccount_changelist")
        else:
            form = OnboardForm()

        context = self.admin_site.each_context(request)
        context.update({"form": form})
        return render(request, "core/onboard_details.html", context)


@admin.register(ElectricVehicle)
class ElectricVehicleAdmin(EntityModelAdmin):
    list_display = ("vin", "license_plate", "brand", "model", "account")
    search_fields = (
        "vin",
        "license_plate",
        "brand__name",
        "model__name",
        "account__name",
    )
    fields = ("account", "vin", "license_plate", "brand", "model")


class WMICodeInline(admin.TabularInline):
    model = WMICode
    extra = 0


@admin.register(Brand)
class BrandAdmin(EntityModelAdmin):
    fields = ("name",)
    list_display = ("name", "wmi_codes_display")
    inlines = [WMICodeInline]

    def wmi_codes_display(self, obj):
        return ", ".join(obj.wmi_codes.values_list("code", flat=True))

    wmi_codes_display.short_description = "WMI codes"


@admin.register(EVModel)
class EVModelAdmin(EntityModelAdmin):
    fields = ("brand", "name")
    list_display = ("name", "brand", "brand_wmi_codes")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("brand").prefetch_related("brand__wmi_codes")

    def brand_wmi_codes(self, obj):
        if not obj.brand:
            return ""
        codes = [wmi.code for wmi in obj.brand.wmi_codes.all()]
        return ", ".join(codes)

    brand_wmi_codes.short_description = "WMI codes"


@admin.register(EnergyCredit)
class EnergyCreditAdmin(EntityModelAdmin):
    list_display = ("account", "amount_kw", "created_by", "created_on")
    readonly_fields = ("created_by", "created_on")

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = "__all__"
        widgets = {"odoo_product": OdooProductWidget}


@admin.register(Product)
class ProductAdmin(EntityModelAdmin):
    form = ProductAdminForm


admin.site.register(LiveSubscription)


class RFIDResource(resources.ModelResource):
    reference = fields.Field(
        column_name="reference",
        attribute="reference",
        widget=ForeignKeyWidget(Reference, "value"),
    )

    class Meta:
        model = RFID
        fields = (
            "label_id",
            "rfid",
            "custom_label",
            "reference",
            "allowed",
            "color",
            "kind",
            "released",
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


@admin.register(RFID)
class RFIDAdmin(EntityModelAdmin, ImportExportModelAdmin):
    change_list_template = "admin/core/rfid/change_list.html"
    resource_class = RFIDResource
    list_display = (
        "label_id",
        "rfid",
        "custom_label",
        "color",
        "kind",
        "released",
        "energy_accounts_display",
        "allowed",
        "added_on",
        "last_seen_on",
    )
    list_filter = ("color", "released", "allowed")
    search_fields = ("label_id", "rfid", "custom_label")
    autocomplete_fields = ["energy_accounts"]
    raw_id_fields = ["reference"]
    actions = ["scan_rfids"]
    readonly_fields = ("added_on", "last_seen_on")
    form = RFIDForm

    def energy_accounts_display(self, obj):
        return ", ".join(str(a) for a in obj.energy_accounts.all())

    energy_accounts_display.short_description = "Energy Accounts"

    def scan_rfids(self, request, queryset):
        return redirect("admin:core_rfid_scan")

    scan_rfids.short_description = "Scan RFIDs"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "report/",
                self.admin_site.admin_view(self.report_view),
                name="core_rfid_report",
            ),
            path(
                "scan/",
                self.admin_site.admin_view(csrf_exempt(self.scan_view)),
                name="core_rfid_scan",
            ),
            path(
                "scan/next/",
                self.admin_site.admin_view(csrf_exempt(self.scan_next)),
                name="core_rfid_scan_next",
            ),
        ]
        return custom + urls

    def report_view(self, request):
        context = self.admin_site.each_context(request)
        context["report"] = ClientReport.build_rows()
        return TemplateResponse(request, "admin/core/rfid/report.html", context)

    def scan_view(self, request):
        context = self.admin_site.each_context(request)
        context["scan_url"] = reverse("admin:core_rfid_scan_next")
        context["admin_change_url_template"] = reverse(
            "admin:core_rfid_change", args=[0]
        )
        return render(request, "admin/core/rfid/scan.html", context)

    def scan_next(self, request):
        from ocpp.rfid.scanner import scan_sources

        result = scan_sources(request)
        status = 500 if result.get("error") else 200
        return JsonResponse(result, status=status)


@admin.register(ClientReport)
class ClientReportAdmin(EntityModelAdmin):
    list_display = ("created_on", "start_date", "end_date")
    readonly_fields = ("created_on", "data")

    change_list_template = "admin/core/clientreport/change_list.html"

    class ClientReportForm(forms.Form):
        PERIOD_CHOICES = [
            ("range", "Date range"),
            ("week", "Week"),
            ("month", "Month"),
        ]
        period = forms.ChoiceField(
            choices=PERIOD_CHOICES, widget=forms.RadioSelect, initial="range"
        )
        start = forms.DateField(
            label="Start date",
            required=False,
            widget=forms.DateInput(attrs={"type": "date"}),
        )
        end = forms.DateField(
            label="End date",
            required=False,
            widget=forms.DateInput(attrs={"type": "date"}),
        )
        week = forms.CharField(
            label="Week",
            required=False,
            widget=forms.TextInput(attrs={"type": "week"}),
        )
        month = forms.DateField(
            label="Month",
            required=False,
            widget=forms.DateInput(attrs={"type": "month"}),
        )

        def clean(self):
            cleaned = super().clean()
            period = cleaned.get("period")
            if period == "range":
                if not cleaned.get("start") or not cleaned.get("end"):
                    raise forms.ValidationError("Please provide start and end dates.")
            elif period == "week":
                week_str = cleaned.get("week")
                if not week_str:
                    raise forms.ValidationError("Please select a week.")
                year, week_num = week_str.split("-W")
                start = datetime.date.fromisocalendar(int(year), int(week_num), 1)
                cleaned["start"] = start
                cleaned["end"] = start + datetime.timedelta(days=6)
            elif period == "month":
                month_dt = cleaned.get("month")
                if not month_dt:
                    raise forms.ValidationError("Please select a month.")
                start = month_dt.replace(day=1)
                last_day = calendar.monthrange(month_dt.year, month_dt.month)[1]
                cleaned["start"] = start
                cleaned["end"] = month_dt.replace(day=last_day)
            return cleaned

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "generate/",
                self.admin_site.admin_view(self.generate_view),
                name="core_clientreport_generate",
            ),
        ]
        return custom + urls

    def generate_view(self, request):
        form = self.ClientReportForm(request.POST or None)
        report = None
        if request.method == "POST" and form.is_valid():
            report = ClientReport.generate(
                form.cleaned_data["start"], form.cleaned_data["end"]
            )
        context = self.admin_site.each_context(request)
        context.update({"form": form, "report": report})
        return TemplateResponse(
            request, "admin/core/clientreport/generate.html", context
        )


@admin.register(PackageRelease)
class PackageReleaseAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    change_list_template = "admin/core/packagerelease/change_list.html"
    list_display = (
        "version",
        "package_link",
        "is_current",
        "pypi_url",
        "revision_short",
        "published_status",
    )
    list_display_links = ("version",)
    actions = ["publish_release", "validate_releases"]
    change_actions = ["publish_release_action"]
    changelist_actions = ["refresh_from_pypi"]
    readonly_fields = ("pypi_url", "is_current", "revision")
    fields = (
        "package",
        "release_manager",
        "version",
        "revision",
        "is_current",
        "pypi_url",
    )

    @admin.display(description="package", ordering="package")
    def package_link(self, obj):
        url = reverse("admin:core_package_change", args=[obj.package_id])
        return format_html('<a href="{}">{}</a>', url, obj.package)

    def revision_short(self, obj):
        return obj.revision_short

    revision_short.short_description = "revision"

    def refresh_from_pypi(self, request, queryset):
        package = Package.objects.filter(is_active=True).first()
        if not package:
            self.message_user(request, "No active package", messages.ERROR)
            return
        try:
            resp = requests.get(
                f"https://pypi.org/pypi/{package.name}/json", timeout=10
            )
            resp.raise_for_status()
        except Exception as exc:  # pragma: no cover - network failure
            self.message_user(request, str(exc), messages.ERROR)
            return
        releases = resp.json().get("releases", {})
        created = 0
        for version in releases:
            exists = PackageRelease.all_objects.filter(
                package=package, version=version
            ).exists()
            if not exists:
                PackageRelease.objects.create(
                    package=package,
                    release_manager=package.release_manager,
                    version=version,
                    revision="",
                    pypi_url=f"https://pypi.org/project/{package.name}/{version}/",
                )
                created += 1
        if created:
            PackageRelease.dump_fixture()
            self.message_user(
                request,
                f"Created {created} release{'s' if created != 1 else ''} from PyPI",
                messages.SUCCESS,
            )
        else:
            self.message_user(request, "No new releases found", messages.INFO)

    refresh_from_pypi.label = "Refresh from PyPI"
    refresh_from_pypi.short_description = "Refresh from PyPI"

    def _publish_release(self, request, release):
        try:
            release.full_clean()
        except ValidationError as exc:
            self.message_user(request, "; ".join(exc.messages), messages.ERROR)
            return
        return redirect(reverse("release-progress", args=[release.pk, "publish"]))

    @admin.action(description="Publish selected release(s)")
    def publish_release(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request, "Select exactly one release to publish", messages.ERROR
            )
            return
        return self._publish_release(request, queryset.first())

    def publish_release_action(self, request, obj):
        return self._publish_release(request, obj)

    publish_release_action.label = "Publish selected Release"
    publish_release_action.short_description = "Publish this release"

    @admin.action(description="Validate selected Releases")
    def validate_releases(self, request, queryset):
        deleted = False
        for release in queryset:
            if not release.pypi_url:
                self.message_user(
                    request,
                    f"{release} has not been published yet",
                    messages.WARNING,
                )
                continue
            url = f"https://pypi.org/pypi/{release.package.name}/{release.version}/json"
            try:
                resp = requests.get(url, timeout=10)
            except Exception as exc:  # pragma: no cover - network failure
                self.message_user(request, f"{release}: {exc}", messages.ERROR)
                continue
            if resp.status_code == 200:
                continue
            release.delete()
            deleted = True
            self.message_user(
                request,
                f"Deleted {release} as it was not found on PyPI",
                messages.WARNING,
            )
        if deleted:
            PackageRelease.dump_fixture()

    @staticmethod
    def _boolean_icon(value: bool) -> str:
        icon = static("admin/img/icon-yes.svg" if value else "admin/img/icon-no.svg")
        alt = "True" if value else "False"
        return format_html('<img src="{}" alt="{}">', icon, alt)

    @admin.display(description="Published")
    def published_status(self, obj):
        return self._boolean_icon(obj.is_published)

    @admin.display(description="Is current")
    def is_current(self, obj):
        return self._boolean_icon(obj.is_current)


@admin.register(Todo)
class TodoAdmin(EntityModelAdmin):
    list_display = ("request", "url")

    def has_add_permission(self, request, obj=None):
        return False

    def get_model_perms(self, request):
        return {}

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        context = super().render_change_form(
            request, context, add=add, change=change, form_url=form_url, obj=obj
        )
        context["show_user_datum"] = False
        context["show_seed_datum"] = False
        context["show_save_as_copy"] = False
        return context
