from collections import defaultdict
from io import BytesIO
import os
from typing import Any

from django import forms
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
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.contrib import messages
from django.contrib.auth import get_user_model
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
from ocpp.models import Charger, ElectricVehicle, Transaction
from ocpp.rfid.utils import build_mode_toggle
from teams.models import EmailInbox, EmailCollector, EmailOutbox
from .github_helper import GitHubRepositoryError, create_repository_for_package
from .models import (
    User,
    UserPhoneNumber,
    CustomerAccount,
    EnergyCredit,
    EnergyTransaction,
    EnergyTariff,
    Location,
    ClientReport,
    ClientReportSchedule,
    Product,
    RFID,
    SigilRoot,
    CustomSigil,
    Reference,
    OdooProfile,
    OpenPayProfile,
    GoogleCalendarProfile,
    SocialProfile,
    Package,
    PackageRelease,
    ReleaseManager,
    SecurityGroup,
    InviteLead,
    PublicWifiAccess,
    Todo,
)
from .user_data import (
    EntityModelAdmin,
    UserDatumAdminMixin,
    delete_user_fixture,
    dump_user_fixture,
    _fixture_path,
    _resolve_fixture_user,
    _user_allows_user_data,
)
from .widgets import OdooProductWidget, RFIDDataWidget
from .rfid_import_export import (
    account_column_for_field,
    parse_accounts,
    serialize_accounts,
)
from . import release as release_utils

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


def get_app_list_with_protocol_forwarder(self, request, app_label=None):
    if app_label == "protocols":
        return _original_admin_get_app_list(self, request, app_label=app_label)

    full_list = list(_original_admin_get_app_list(self, request, app_label=None))
    merged_list = []
    ocpp_entry = None
    protocols_entry = None

    for entry in full_list:
        label = entry.get("app_label")
        if label == "ocpp":
            ocpp_entry = entry
            merged_list.append(entry)
        elif label == "protocols":
            protocols_entry = entry
        else:
            merged_list.append(entry)

    result = merged_list
    if ocpp_entry and protocols_entry and protocols_entry.get("models"):
        cp_models = [model.copy() for model in protocols_entry["models"]]
        existing = {model["object_name"] for model in ocpp_entry["models"]}
        additional = [model for model in cp_models if model["object_name"] not in existing]
        if additional:
            ocpp_entry["models"].extend(additional)
            ocpp_entry["models"].sort(key=lambda model: model["name"])
        if protocols_entry.get("has_module_perms"):
            ocpp_entry["has_module_perms"] = True
    else:
        result = full_list

    if app_label:
        return [entry for entry in result if entry.get("app_label") == app_label]
    return result


admin.AdminSite.get_app_list = get_app_list_with_protocol_forwarder


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


class ProfileAdminMixin:
    """Reusable actions for profile-bound admin classes."""

    def _get_user_profile_info(self, request):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return user, None, 0

        queryset = self.model._default_manager.filter(user=user)
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

    def get_actions(self, request):
        actions = super().get_actions(request)
        if "my_profile" not in actions:
            action = getattr(self, "my_profile", None)
            if action is not None:
                actions["my_profile"] = (
                    action,
                    "my_profile",
                    getattr(action, "short_description", _("Active Profile")),
                )
        return actions


@admin.register(ExperienceReference)
class ReferenceAdmin(EntityModelAdmin):
    list_display = (
        "alt_text",
        "content_type",
        "link",
        "header",
        "footer",
        "visibility",
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
        "roles",
        "features",
        "sites",
        "include_in_footer",
        "show_in_header",
        "footer_visibility",
        "transaction_uuid",
        "author",
        "uses",
        "qr_code",
    )
    filter_horizontal = ("roles", "features", "sites")

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj:
            ro.append("transaction_uuid")
        return ro

    @admin.display(description="Footer", boolean=True, ordering="include_in_footer")
    def footer(self, obj):
        return obj.include_in_footer

    @admin.display(description="Header", boolean=True, ordering="show_in_header")
    def header(self, obj):
        return obj.show_in_header

    @admin.display(description="Visibility", ordering="footer_visibility")
    def visibility(self, obj):
        return obj.get_footer_visibility_display()

    @admin.display(description="LINK")
    def link(self, obj):
        if obj.value:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">open</a>',
                obj.value,
            )
        return ""

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
            "git_password": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pypi_token"].help_text = format_html(
            "{} <a href=\"{}\" target=\"_blank\" rel=\"noopener noreferrer\">{}</a>{}",
            "Generate an API token from your PyPI account settings.",
            "https://pypi.org/manage/account/token/",
            "pypi.org/manage/account/token/",
            (
                " by clicking “Add API token”, optionally scoping it to the package, "
                "and paste the full `pypi-***` value here."
            ),
        )
        self.fields["github_token"].help_text = format_html(
            "{} <a href=\"{}\" target=\"_blank\" rel=\"noopener noreferrer\">{}</a>{}",
            "Create a personal access token at GitHub → Settings → Developer settings →",
            "https://github.com/settings/tokens",
            "github.com/settings/tokens",
            (
                " with the repository access needed for releases (repo scope for classic tokens "
                "or an equivalent fine-grained token) and paste it here."
            ),
        )
        self.fields["git_username"].help_text = (
            "Username used for HTTPS git pushes (for example, your GitHub username)."
        )
        self.fields["git_password"].help_text = format_html(
            "{} <a href=\"{}\" target=\"_blank\" rel=\"noopener noreferrer\">{}</a>{}",
            "Provide the password or personal access token used for pushing tags. ",
            "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token",
            "docs.github.com/.../creating-a-personal-access-token",
            " If left blank, the GitHub token will be used instead.",
        )


@admin.register(ReleaseManager)
class ReleaseManagerAdmin(ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin):
    form = ReleaseManagerAdminForm
    list_display = ("owner", "pypi_username", "pypi_url", "secondary_pypi_url")
    actions = ["test_credentials"]
    change_actions = ["test_credentials_action", "my_profile_action"]
    changelist_actions = ["my_profile"]
    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        (
            "PyPI",
            {
                "fields": (
                    "pypi_username",
                    "pypi_token",
                    "pypi_password",
                    "pypi_url",
                    "secondary_pypi_url",
                )
            },
        ),
        (
            "GitHub",
            {
                "fields": (
                    "github_token",
                    "git_username",
                    "git_password",
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
        env_url = os.environ.get("PYPI_REPOSITORY_URL", "").strip()
        url = env_url or "https://upload.pypi.org/legacy/"
        auth = (
            ("__token__", creds.token)
            if creds.token
            else (creds.username, creds.password)
        )
        try:
            resp = requests.post(
                url,
                auth=auth,
                data={"verify_credentials": "1"},
                timeout=10,
                allow_redirects=False,
            )
            status = resp.status_code
            if status in {401, 403}:
                self.message_user(
                    request,
                    f"{manager} credentials invalid ({status})",
                    messages.ERROR,
                )
            elif status <= 400:
                suffix = f" ({status})" if status != 200 else ""
                self.message_user(
                    request,
                    f"{manager} credentials valid{suffix}",
                    messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    f"{manager} credentials check returned unexpected status {status}",
                    messages.ERROR,
                )
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request, f"{manager} credentials check failed: {exc}", messages.ERROR
            )


class PackageRepositoryForm(forms.Form):
    owner_repo = forms.CharField(
        label=_("Owner/Repository"),
        help_text=_("Enter the repository slug in the form owner/repository."),
        widget=forms.TextInput(attrs={"placeholder": "owner/repository"}),
    )
    description = forms.CharField(
        label=_("Description"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    private = forms.BooleanField(
        label=_("Private repository"),
        required=False,
        help_text=_("Mark the repository as private when checked."),
    )

    def clean_owner_repo(self):
        value = self.cleaned_data.get("owner_repo", "").strip()
        if "/" not in value:
            raise forms.ValidationError(_("Enter the owner/repository slug."))
        owner, repo = value.split("/", 1)
        owner = owner.strip()
        repo = repo.strip()
        if not owner or not repo:
            raise forms.ValidationError(_("Enter the owner/repository slug."))
        if " " in owner or " " in repo:
            raise forms.ValidationError(
                _("Owner and repository cannot contain spaces."),
            )
        self.cleaned_data["owner"] = owner
        self.cleaned_data["repo"] = repo
        return value


@admin.register(Package)
class PackageAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    actions = ["create_repository_bulk_action"]
    list_display = (
        "name",
        "description",
        "homepage_url",
        "release_manager",
        "is_active",
    )
    change_actions = ["create_repository_action", "prepare_next_release_action"]

    def _prepare(self, request, package):
        if request.method not in {"POST", "GET"}:
            return HttpResponseNotAllowed(["GET", "POST"])
        from pathlib import Path
        from packaging.version import Version

        ver_file = Path("VERSION")
        if ver_file.exists():
            raw_version = ver_file.read_text().strip()
            cleaned_version = raw_version.rstrip("+") or "0.0.0"
            repo_version = Version(cleaned_version)
        else:
            repo_version = Version("0.0.0")

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
                "<int:object_id>/create-repository/",
                self.admin_site.admin_view(self.create_repository_view),
                name="core_package_create_repository",
            ),
            path(
                "prepare-next-release/",
                self.admin_site.admin_view(self.prepare_next_release_active),
                name="core_package_prepare_next_release",
            )
        ]
        return custom + urls

    def create_repository_action(self, request, obj):
        url = reverse("admin:core_package_create_repository", args=[obj.pk])
        return redirect(url)

    create_repository_action.label = _("Create GitHub repository")
    create_repository_action.short_description = _("Create GitHub repository")

    def prepare_next_release_active(self, request):
        package = Package.objects.filter(is_active=True).first()
        if not package:
            self.message_user(request, "No active package", messages.ERROR)
            return redirect("admin:core_package_changelist")
        return self._prepare(request, package)

    def prepare_next_release_action(self, request, obj):
        return self._prepare(request, obj)

    prepare_next_release_action.label = "Prepare next Release"
    prepare_next_release_action.short_description = "Prepare next release"

    @admin.action(description=_("Create GitHub repository"))
    def create_repository_bulk_action(self, request, queryset):
        selected = list(queryset[:2])
        if len(selected) != 1:
            self.message_user(
                request,
                _("Select exactly one package to create a GitHub repository."),
                messages.WARNING,
            )
            return None

        package = selected[0]
        url = reverse("admin:core_package_create_repository", args=[package.pk])
        return redirect(url)

    @staticmethod
    def _slug_from_repository_url(repository_url: str) -> str:
        if not repository_url:
            return ""
        if repository_url.startswith("git@"):
            path = repository_url.partition(":")[2]
        else:
            parsed = urlparse(repository_url)
            path = parsed.path
        path = path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) >= 2:
            return "/".join(segments[-2:])
        return ""

    def _repository_form_initial(self, package: Package) -> dict[str, object]:
        initial: dict[str, object] = {"description": package.description}
        slug = self._slug_from_repository_url(package.repository_url)
        if slug:
            initial["owner_repo"] = slug
        return initial

    def create_repository_view(self, request, object_id: int):
        package = get_object_or_404(Package, pk=object_id)

        if request.method == "POST":
            form = PackageRepositoryForm(request.POST)
            if form.is_valid():
                description = form.cleaned_data.get("description") or None
                try:
                    repository_url = create_repository_for_package(
                        package,
                        owner=form.cleaned_data["owner"],
                        repo=form.cleaned_data["repo"],
                        private=form.cleaned_data.get("private", False),
                        description=description,
                    )
                except GitHubRepositoryError as exc:
                    self.message_user(
                        request,
                        _("GitHub repository creation failed: %s") % exc,
                        messages.ERROR,
                    )
                except Exception as exc:  # pragma: no cover - defensive guard
                    logger.exception(
                        "Unexpected error while creating GitHub repository for %s",
                        package,
                    )
                    self.message_user(
                        request,
                        _("GitHub repository creation failed: %s") % exc,
                        messages.ERROR,
                    )
                else:
                    package.repository_url = repository_url
                    package.save(update_fields=["repository_url"])
                    self.message_user(
                        request,
                        _("GitHub repository created: %s") % repository_url,
                        messages.SUCCESS,
                    )
                    change_url = reverse(
                        "admin:core_package_change", args=[package.pk]
                    )
                    return redirect(change_url)
        else:
            form = PackageRepositoryForm(initial=self._repository_form_initial(package))

        context = self.admin_site.each_context(request)
        context.update(
            {
                "opts": self.model._meta,
                "original": package,
                "title": _("Create GitHub repository"),
                "form": form,
            }
        )
        return TemplateResponse(
            request, "admin/core/package/create_repository.html", context
        )


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
    search_fields = ("name", "parent__name")


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


@admin.register(PublicWifiAccess)
class PublicWifiAccessAdmin(EntityModelAdmin):
    list_display = ("user", "mac_address", "created_on", "revoked_on")
    search_fields = ("user__username", "mac_address")
    readonly_fields = ("user", "mac_address", "created_on", "updated_on", "revoked_on")
    ordering = ("-created_on",)


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
            return keep_existing("password")
        return pwd

    def _post_clean(self):
        super()._post_clean()
        _restore_sigil_values(
            self,
            ["host", "database", "username", "password"],
        )


class OpenPayProfileAdminForm(forms.ModelForm):
    """Admin form for :class:`core.models.OpenPayProfile` with masked secrets."""

    private_key = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave blank to keep the current key.",
    )
    webhook_secret = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave blank to keep the current secret.",
    )
    paypal_client_secret = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave blank to keep the current secret.",
    )
    paypal_webhook_id = forms.CharField(
        required=False,
        help_text="Leave blank to keep the current webhook identifier.",
    )

    class Meta:
        model = OpenPayProfile
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        openpay_help = _(
            "Provide merchant ID, public and private keys, and webhook secret from OpenPay."
        )
        self.fields["merchant_id"].help_text = openpay_help
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
        default_processor_field = self.fields.get("default_processor")
        if default_processor_field is not None:
            default_processor_field.help_text = _(
                "Select which configured processor to try first when charging."
            )
        self.fields["paypal_client_id"].help_text = _(
            "PayPal REST client ID for your application."
        )
        self.fields["paypal_client_secret"].help_text = _(
            "PayPal REST client secret. Leave blank to keep the current secret."
        )
        self.fields["paypal_webhook_id"].help_text = _(
            "PayPal webhook ID used to validate notifications. Leave blank to keep the current webhook identifier."
        )
        self.fields["paypal_is_production"].help_text = _(
            "Enable to send requests to PayPal's live environment."
        )

        if self.instance.pk:
            for field in ("private_key", "webhook_secret", "paypal_client_secret", "paypal_webhook_id"):
                if field in self.fields:
                    self.fields[field].initial = ""
                    self.initial[field] = ""

    def clean_private_key(self):
        key = self.cleaned_data.get("private_key")
        if not key and self.instance.pk:
            return keep_existing("private_key")
        return key

    def clean_webhook_secret(self):
        secret = self.cleaned_data.get("webhook_secret")
        if secret == "" and self.instance.pk:
            return keep_existing("webhook_secret")
        return secret

    def clean_paypal_client_secret(self):
        secret = self.cleaned_data.get("paypal_client_secret")
        if not secret and self.instance.pk:
            return keep_existing("paypal_client_secret")
        return secret

    def clean_paypal_webhook_id(self):
        identifier = self.cleaned_data.get("paypal_webhook_id")
        if identifier == "" and self.instance.pk:
            return keep_existing("paypal_webhook_id")
        return identifier

    def _post_clean(self):
        super()._post_clean()
        _restore_sigil_values(
            self,
            [
                "merchant_id",
                "private_key",
                "public_key",
                "webhook_secret",
                "paypal_client_id",
                "paypal_client_secret",
                "paypal_webhook_id",
            ],
        )


class GoogleCalendarProfileAdminForm(forms.ModelForm):
    """Admin form for :class:`core.models.GoogleCalendarProfile`."""

    api_key = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave blank to keep the current key.",
    )

    class Meta:
        model = GoogleCalendarProfile
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["api_key"].initial = ""
            self.initial["api_key"] = ""
        else:
            self.fields["api_key"].required = True

    def clean_api_key(self):
        key = self.cleaned_data.get("api_key")
        if not key and self.instance.pk:
            return keep_existing("api_key")
        return key

    def _post_clean(self):
        super()._post_clean()
        _restore_sigil_values(
            self,
            ["calendar_id", "api_key", "display_name", "timezone"],
        )


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
    """Admin form for :class:`teams.models.EmailInbox` with hidden password."""

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
                "Provide host, database, username, and password to create a CRM employee.",
            )

        return cleaned


class OpenPayProfileInlineForm(ProfileFormMixin, OpenPayProfileAdminForm):
    profile_fields = OpenPayProfile.profile_fields

    class Meta(OpenPayProfileAdminForm.Meta):
        exclude = ("user", "group", "verified_on", "verification_reference")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE") or self.errors:
            return cleaned

        def _has_value(name: str) -> bool:
            value = cleaned.get(name)
            if isinstance(value, KeepExistingValue):
                return bool(getattr(self.instance, name))
            return not self._is_empty_value(value)

        openpay_fields = ("merchant_id", "private_key", "public_key")
        openpay_provided = [name for name in openpay_fields if _has_value(name)]
        openpay_missing = [name for name in openpay_fields if not _has_value(name)]
        if openpay_provided and openpay_missing:
            raise forms.ValidationError(
                _(
                    "Provide merchant ID, private key, and public key to configure OpenPay."
                )
            )

        paypal_fields = ("paypal_client_id", "paypal_client_secret")
        paypal_provided = [name for name in paypal_fields if _has_value(name)]
        paypal_missing = [name for name in paypal_fields if not _has_value(name)]
        if paypal_provided and paypal_missing:
            raise forms.ValidationError(
                _("Provide PayPal client ID and client secret to configure PayPal.")
            )

        has_openpay = len(openpay_provided) == len(openpay_fields)
        has_paypal = len(paypal_provided) == len(paypal_fields)

        if not has_openpay and not has_paypal:
            raise forms.ValidationError(
                _("Provide OpenPay or PayPal credentials to configure a payment processor.")
            )

        default_processor = cleaned.get("default_processor") or OpenPayProfile.PROCESSOR_OPENPAY
        if default_processor == OpenPayProfile.PROCESSOR_OPENPAY and not has_openpay:
            raise forms.ValidationError(
                _(
                    "OpenPay must be fully configured or select PayPal as the default processor."
                )
            )
        if default_processor == OpenPayProfile.PROCESSOR_PAYPAL and not has_paypal:
            raise forms.ValidationError(
                _(
                    "PayPal must be fully configured or select OpenPay as the default processor."
                )
            )
        return cleaned


class GoogleCalendarProfileInlineForm(
    ProfileFormMixin, GoogleCalendarProfileAdminForm
):
    profile_fields = GoogleCalendarProfile.profile_fields

    class Meta(GoogleCalendarProfileAdminForm.Meta):
        exclude = ("user", "group")


class EmailInboxInlineForm(ProfileFormMixin, EmailInboxAdminForm):
    profile_fields = EmailInbox.profile_fields

    class Meta(EmailInboxAdminForm.Meta):
        exclude = ("user", "group")


class SocialProfileInlineForm(ProfileFormMixin, forms.ModelForm):
    profile_fields = SocialProfile.profile_fields

    class Meta:
        model = SocialProfile
        fields = (
            "network",
            "handle",
            "domain",
            "did",
            "application_id",
            "public_key",
            "guild_id",
            "bot_token",
            "default_channel_id",
        )


class EmailOutboxAdminForm(MaskedPasswordFormMixin, forms.ModelForm):
    """Admin form for :class:`teams.models.EmailOutbox` with hidden password."""

    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        required=False,
        help_text="Leave blank to keep the current password.",
    )
    password_sigil_fields = ("password", "host", "username", "from_email")

    class Meta:
        model = EmailOutbox
        fields = "__all__"


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


class ReleaseManagerInlineForm(ProfileFormMixin, forms.ModelForm):
    profile_fields = ReleaseManager.profile_fields

    class Meta:
        model = ReleaseManager
        fields = (
            "pypi_username",
            "pypi_token",
            "github_token",
            "git_username",
            "git_password",
            "pypi_password",
            "pypi_url",
            "secondary_pypi_url",
        )
        widgets = {
            "pypi_token": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
            "github_token": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
            "git_password": forms.Textarea(attrs={"rows": 3, "style": "width: 40em;"}),
        }


PROFILE_INLINE_CONFIG = {
    OdooProfile: {
        "form": OdooProfileInlineForm,
        "fieldsets": (
            (
                None,
                {
                    "fields": (
                        "crm",
                        "host",
                        "database",
                        "username",
                        "password",
                    )
                },
            ),
            (
                "CRM Employee",
                {
                    "fields": ("verified_on", "odoo_uid", "name", "email"),
                },
            ),
        ),
        "readonly_fields": ("verified_on", "odoo_uid", "name", "email"),
    },
    OpenPayProfile: {
        "form": OpenPayProfileInlineForm,
        "fieldsets": (
            (
                _("Default Processor"),
                {
                    "fields": ("default_processor",),
                    "description": _(
                        "Choose which configured processor to contact first when processing payments."
                    ),
                },
            ),
            (
                None,
                {
                    "fields": (
                        "merchant_id",
                        "public_key",
                        "private_key",
                        "webhook_secret",
                        "is_production",
                    )
                },
            ),
            (
                _("PayPal"),
                {
                    "fields": (
                        "paypal_client_id",
                        "paypal_client_secret",
                        "paypal_webhook_id",
                        "paypal_is_production",
                    ),
                    "description": _("Configure PayPal REST API access."),
                },
            ),
            (
                _("Verification"),
                {"fields": ("verified_on", "verification_reference")},
            ),
        ),
        "readonly_fields": ("verified_on", "verification_reference"),
    },
    GoogleCalendarProfile: {
        "form": GoogleCalendarProfileInlineForm,
        "fields": (
            "display_name",
            "calendar_id",
            "api_key",
            "max_events",
            "timezone",
        ),
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
    SocialProfile: {
        "form": SocialProfileInlineForm,
        "fieldsets": (
            (
                _("Network"),
                {
                    "fields": ("network",),
                },
            ),
            (
                _("Configuration: Bluesky"),
                {
                    "fields": ("handle", "domain", "did"),
                    "description": _(
                        "1. Set your Bluesky handle to the domain managed by Arthexis. "
                        "2. Publish a _atproto TXT record or /.well-known/atproto-did file pointing to the DID below. "
                        "3. Save once Bluesky confirms the domain matches the DID."
                    ),
                },
            ),
            (
                _("Configuration: Discord"),
                {
                    "fields": (
                        "application_id",
                        "public_key",
                        "guild_id",
                        "bot_token",
                        "default_channel_id",
                    ),
                    "description": _(
                        "Provide the Discord application and guild identifiers plus a bot token so Arthexis can control the bot. "
                        "The public key verifies interaction requests and the default channel is optional."
                    ),
                },
            ),
        ),
        "fieldset_visibility": (
            {
                "name": _("Configuration: Bluesky"),
                "field": "network",
                "values": (SocialProfile.Network.BLUESKY,),
            },
            {
                "name": _("Configuration: Discord"),
                "field": "network",
                "values": (SocialProfile.Network.DISCORD,),
            },
        ),
    },
    ReleaseManager: {
        "form": ReleaseManagerInlineForm,
        "fields": (
            "pypi_username",
            "pypi_token",
            "github_token",
            "pypi_password",
            "pypi_url",
            "secondary_pypi_url",
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
    OdooProfile,
    OpenPayProfile,
    EmailInbox,
    EmailOutbox,
    SocialProfile,
    ReleaseManager,
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


@admin.register(User)
class UserAdmin(UserDatumAdminMixin, DjangoUserAdmin):
    form = UserChangeRFIDForm
    fieldsets = _append_operate_as(DjangoUserAdmin.fieldsets)
    add_fieldsets = _append_operate_as(DjangoUserAdmin.add_fieldsets)
    inlines = USER_PROFILE_INLINES + [UserPhoneNumberInline]
    change_form_template = "admin/user_profile_change_form.html"
    _skip_entity_user_datum = True

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


@admin.register(SocialProfile)
class SocialProfileAdmin(
    ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin
):
    list_display = ("owner", "network", "handle", "domain", "guild_id")
    list_filter = ("network",)
    search_fields = ("handle", "domain", "did", "application_id", "guild_id")
    changelist_actions = ["my_profile"]
    change_actions = ["my_profile_action"]
    fieldsets = (
        (_("Owner"), {"fields": ("user", "group")}),
        (_("Network"), {"fields": ("network",)}),
        (
            _("Configuration: Bluesky"),
            {
                "fields": ("handle", "domain", "did"),
                "description": _(
                    "Link Arthexis to Bluesky by using a verified domain handle. "
                    "Publish a _atproto TXT record or /.well-known/atproto-did file "
                    "that returns the DID stored here before saving."
                ),
            },
        ),
        (
            _("Configuration: Discord"),
            {
                "fields": (
                    "application_id",
                    "public_key",
                    "guild_id",
                    "bot_token",
                    "default_channel_id",
                ),
                "description": _(
                    "Store the Discord application and guild identifiers plus the bot token "
                    "used for automation. The public key verifies interaction callbacks and the "
                    "default channel is optional."
                ),
            },
        ),
    )

    @admin.display(description=_("Owner"))
    def owner(self, obj):
        return obj.owner_display()


@admin.register(OdooProfile)
class OdooProfileAdmin(ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin):
    change_form_template = "django_object_actions/change_form.html"
    form = OdooProfileAdminForm
    list_display = ("owner", "host", "database", "credentials_ok", "verified_on")
    list_filter = ("crm",)
    readonly_fields = ("verified_on", "odoo_uid", "name", "email")
    actions = ["verify_credentials"]
    change_actions = ["verify_credentials_action", "my_profile_action"]
    changelist_actions = ["my_profile", "generate_quote_report"]
    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        ("Configuration", {"fields": ("crm", "host", "database")}),
        ("Credentials", {"fields": ("username", "password")}),
        (
            "CRM Employee",
            {"fields": ("verified_on", "odoo_uid", "name", "email")},
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

    @admin.action(description="Test credentials")
    def verify_credentials(self, request, queryset):
        for profile in queryset:
            self._verify_credentials(request, profile)

    def verify_credentials_action(self, request, obj):
        self._verify_credentials(request, obj)

    verify_credentials_action.label = "Test credentials"
    verify_credentials_action.short_description = "Test credentials"

    def generate_quote_report(self, request, queryset=None):
        return HttpResponseRedirect(reverse("odoo-quote-report"))

    generate_quote_report.label = _("Quote Report")
    generate_quote_report.short_description = _("Quote Report")


@admin.register(OpenPayProfile)
class OpenPayProfileAdmin(ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin):
    change_form_template = "django_object_actions/change_form.html"
    form = OpenPayProfileAdminForm
    list_display = ("owner", "default_processor_display", "environment", "verified_on")
    readonly_fields = ("verified_on", "verification_reference")
    actions = ["verify_credentials"]
    change_actions = ["verify_credentials_action", "my_profile_action"]
    changelist_actions = ["my_profile"]
    fieldsets = (
        (_("Owner"), {"fields": ("user", "group")}),
        (
            _("Default Processor"),
            {
                "fields": ("default_processor",),
                "description": _(
                    "Choose which configured processor to contact first when processing payments."
                ),
            },
        ),
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
            _("PayPal"),
            {
                "fields": (
                    "paypal_client_id",
                    "paypal_client_secret",
                    "paypal_webhook_id",
                    "paypal_is_production",
                ),
                "description": _("Configure PayPal REST API access."),
            },
        ),
        (
            _("Verification"),
            {"fields": ("verified_on", "verification_reference")},
        ),
    )

    @admin.display(description=_("Owner"))
    def owner(self, obj):
        return obj.owner_display()

    @admin.display(description=_("Default Processor"))
    def default_processor_display(self, obj):
        return obj.get_default_processor_display()

    @admin.display(description=_("Environment"))
    def environment(self, obj):
        if obj.default_processor == obj.PROCESSOR_PAYPAL:
            return _("PayPal Production") if obj.paypal_is_production else _("PayPal Sandbox")
        return _("OpenPay Production") if obj.is_production else _("OpenPay Sandbox")

    def _verify_credentials(self, request, profile):
        owner = (
            profile.owner_display()
            or profile.merchant_id
            or profile.paypal_client_id
            or _("Payment Processor")
        )
        try:
            profile.verify()
        except ValidationError as exc:
            message = "; ".join(exc.messages)
            self.message_user(
                request,
                f"{owner}: {message}",
                level=messages.ERROR,
            )
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(
                request,
                f"{owner}: {exc}",
                level=messages.ERROR,
            )
        else:
            self.message_user(
                request,
                _("%(owner)s verified") % {"owner": owner},
                level=messages.SUCCESS,
            )

    @admin.action(description=_("Test credentials"))
    def verify_credentials(self, request, queryset):
        for profile in queryset:
            self._verify_credentials(request, profile)

    def verify_credentials_action(self, request, obj):
        self._verify_credentials(request, obj)

    verify_credentials_action.label = _("Test credentials")
    verify_credentials_action.short_description = _("Test credentials")


class GoogleCalendarProfileAdmin(
    ProfileAdminMixin, SaveBeforeChangeAction, EntityModelAdmin
):
    form = GoogleCalendarProfileAdminForm
    list_display = ("owner", "calendar_identifier", "max_events")
    search_fields = (
        "display_name",
        "calendar_id",
        "user__username",
        "group__name",
    )
    changelist_actions = ["my_profile"]
    change_actions = ["my_profile_action"]
    fieldsets = (
        (_("Owner"), {"fields": ("user", "group")}),
        (
            _("Calendar"),
            {
                "fields": (
                    "display_name",
                    "calendar_id",
                    "api_key",
                    "max_events",
                    "timezone",
                )
            },
        ),
    )

    @admin.display(description=_("Owner"))
    def owner(self, obj):
        return obj.owner_display()

    @admin.display(description=_("Calendar"))
    def calendar_identifier(self, obj):
        display = obj.get_display_name()
        return display or obj.resolved_calendar_id()

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
    list_display = ("owner_label", "username", "host", "protocol")
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
                name="teams_emailinbox_test",
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
                "admin:teams_emailinbox_test", args=[object_id]
            )
        return super().changeform_view(request, object_id, form_url, extra_context)

    fieldsets = (
        ("Owner", {"fields": ("user", "group")}),
        ("Credentials", {"fields": ("username", "password")}),
        (
            "Configuration",
            {"fields": ("host", "port", "protocol", "use_ssl")},
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

    # Onboarding wizard view
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "onboard/",
                self.admin_site.admin_view(self.onboard_details),
                name="core_customeraccount_onboard_details",
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
        "contract_type",
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


class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = "__all__"
        widgets = {"odoo_product": OdooProductWidget}


@admin.register(Product)
class ProductAdmin(EntityModelAdmin):
    form = ProductAdminForm
    actions = ["register_from_odoo"]
    change_list_template = "admin/core/product/change_list.html"

    def _odoo_profile_admin(self):
        return self.admin_site._registry.get(OdooProfile)

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

        profile_admin = self._odoo_profile_admin()
        if profile_admin is not None:
            context["profile_url"] = profile_admin.get_my_profile_url(request)

        profile = getattr(request.user, "odoo_profile", None)
        if not profile or not profile.is_verified:
            context["credential_error"] = _(
                "Configure your CRM employee credentials before registering products."
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


@admin.register(RFID)
class RFIDAdmin(EntityModelAdmin, ImportExportModelAdmin):
    change_list_template = "admin/core/rfid/change_list.html"
    resource_class = RFIDResource
    import_form_class = RFIDImportForm
    confirm_form_class = RFIDConfirmImportForm
    export_form_class = RFIDExportForm
    list_display = (
        "label",
        "rfid",
        "color",
        "kind",
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
        "toggle_selected_user_data",
        "toggle_selected_released",
        "toggle_selected_allowed",
    ]
    readonly_fields = ("added_on", "last_seen_on", "reversed_uid")
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
        return redirect("admin:core_rfid_scan")

    scan_rfids.short_description = "Scan RFIDs"

    @admin.action(description=_("Toggle selected User Data"))
    def toggle_selected_user_data(self, request, queryset):
        toggled = 0
        skipped = 0
        for tag in queryset:
            manager = getattr(type(tag), "all_objects", type(tag).objects)
            target_user = _resolve_fixture_user(tag, request.user)
            allow_user_data = _user_allows_user_data(target_user)
            if tag.is_user_data:
                manager.filter(pk=tag.pk).update(is_user_data=False)
                tag.is_user_data = False
                delete_user_fixture(tag, target_user)
                toggled += 1
                continue
            if not allow_user_data:
                skipped += 1
                continue
            manager.filter(pk=tag.pk).update(is_user_data=True)
            tag.is_user_data = True
            dump_user_fixture(tag, target_user)
            toggled += 1

        if toggled:
            self.message_user(
                request,
                ngettext(
                    "Toggled user data for %(count)d RFID.",
                    "Toggled user data for %(count)d RFIDs.",
                    toggled,
                )
                % {"count": toggled},
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d RFID because user data is not available.",
                    "Skipped %(count)d RFIDs because user data is not available.",
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
                        reverse("admin:core_rfid_change", args=[new_tag.pk])
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
        return TemplateResponse(request, "admin/core/rfid/copy.html", context)

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
        changelist_url = reverse("admin:core_rfid_changelist")
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
                name="core_rfid_report",
            ),
            path(
                "print-valid-labels/",
                self.admin_site.admin_view(self.print_valid_card_labels),
                name="core_rfid_print_valid_card_labels",
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
        context["report"] = ClientReport.build_rows(for_display=True)
        return TemplateResponse(request, "admin/core/rfid/report.html", context)

    def scan_view(self, request):
        context = self.admin_site.each_context(request)
        table_mode, toggle_url, toggle_label = build_mode_toggle(request)
        public_view_url = reverse("rfid-reader")
        if table_mode:
            public_view_url = f"{public_view_url}?mode=table"
        context.update(
            {
                "scan_url": reverse("admin:core_rfid_scan_next"),
                "admin_change_url_template": reverse(
                    "admin:core_rfid_change", args=[0]
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
        return render(request, "admin/core/rfid/scan.html", context)

    def scan_next(self, request):
        from ocpp.rfid.scanner import scan_sources
        from ocpp.rfid.reader import validate_rfid_value

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
        start = forms.DateField(
            label="Start date",
            required=False,
            widget=forms.DateInput(attrs={"type": "date"}),
            help_text="First day included when using a custom date range.",
        )
        end = forms.DateField(
            label="End date",
            required=False,
            widget=forms.DateInput(attrs={"type": "date"}),
            help_text="Last day included when using a custom date range.",
        )
        week = forms.CharField(
            label="Week",
            required=False,
            widget=forms.TextInput(attrs={"type": "week"}),
            help_text="Generates the report for the ISO week that you select.",
        )
        month = forms.DateField(
            label="Month",
            required=False,
            widget=forms.DateInput(attrs={"type": "month"}),
            input_formats=["%Y-%m"],
            help_text="Generates the report for the calendar month that you select.",
        )
        view_mode = forms.ChoiceField(
            label=_("Report layout"),
            choices=VIEW_CHOICES,
            initial="expanded",
            widget=forms.RadioSelect,
            help_text=_(
                "Choose between detailed charge point sections or a combined summary table."
            ),
        )
        language = forms.ChoiceField(
            label="Report language",
            choices=settings.LANGUAGES,
            help_text="Choose the language used for the generated report.",
        )
        title = forms.CharField(
            label="Report title",
            required=False,
            max_length=200,
            help_text="Optional heading that replaces the default report title.",
        )
        chargers = forms.ModelMultipleChoiceField(
            label="Charge points",
            queryset=Charger.objects.filter(connector_id__isnull=True)
            .order_by("display_name", "charger_id"),
            required=False,
            widget=forms.CheckboxSelectMultiple,
            help_text="Choose which charge points are included in the report.",
        )
        owner = forms.ModelChoiceField(
            queryset=get_user_model().objects.all(),
            required=False,
            help_text="Sets who owns the report schedule and is listed as the requestor.",
        )
        destinations = forms.CharField(
            label="Email destinations",
            required=False,
            widget=forms.Textarea(attrs={"rows": 2}),
            help_text="Separate addresses with commas or new lines.",
        )
        recurrence = forms.ChoiceField(
            label="Recurrency",
            choices=RECURRENCE_CHOICES,
            initial=ClientReportSchedule.PERIODICITY_NONE,
            help_text="Defines how often the report should be generated automatically.",
        )
        enable_emails = forms.BooleanField(
            label="Enable email delivery",
            required=False,
            help_text="Send the report via email to the recipients listed above.",
        )

        def __init__(self, *args, request=None, **kwargs):
            self.request = request
            super().__init__(*args, **kwargs)
            if (
                request
                and getattr(request, "user", None)
                and request.user.is_authenticated
            ):
                self.fields["owner"].initial = request.user.pk
            self.fields["chargers"].widget.attrs["class"] = "charger-options"
            if not self.is_bound:
                queryset = self.fields["chargers"].queryset
                self.fields["chargers"].initial = list(
                    queryset.values_list("pk", flat=True)
                )
            language_initial = ClientReport.default_language()
            if request:
                language_initial = ClientReport.normalize_language(
                    getattr(request, "LANGUAGE_CODE", language_initial)
                )
            self.fields["language"].initial = language_initial

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

        def clean_destinations(self):
            raw = self.cleaned_data.get("destinations", "")
            if not raw:
                return []
            validator = EmailValidator()
            seen: set[str] = set()
            emails: list[str] = []
            for part in re.split(r"[\s,]+", raw):
                candidate = part.strip()
                if not candidate:
                    continue
                validator(candidate)
                key = candidate.lower()
                if key in seen:
                    continue
                seen.add(key)
                emails.append(candidate)
            return emails

        def clean_title(self):
            title = self.cleaned_data.get("title")
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
                "generate/action/",
                self.admin_site.admin_view(self.generate_report),
                name="core_clientreport_generate_report",
            ),
            path(
                "download/<int:report_id>/",
                self.admin_site.admin_view(self.download_view),
                name="core_clientreport_download",
            ),
        ]
        return custom + urls

    def generate_view(self, request):
        form = self.ClientReportForm(request.POST or None, request=request)
        report = None
        schedule = None
        download_url = None
        report_rows = None
        report_summary_rows: list[dict[str, Any]] = []
        if request.method == "POST" and form.is_valid():
            owner = form.cleaned_data.get("owner")
            if not owner and request.user.is_authenticated:
                owner = request.user
            enable_emails = form.cleaned_data.get("enable_emails", False)
            disable_emails = not enable_emails
            recipients = form.cleaned_data.get("destinations") if enable_emails else []
            chargers = list(form.cleaned_data.get("chargers") or [])
            language = form.cleaned_data.get("language")
            title = form.cleaned_data.get("title")
            report = ClientReport.generate(
                form.cleaned_data["start"],
                form.cleaned_data["end"],
                owner=owner,
                recipients=recipients,
                disable_emails=disable_emails,
                chargers=chargers,
                language=language,
                title=title,
            )
            report.store_local_copy()
            if chargers:
                report.chargers.set(chargers)
            if enable_emails and recipients:
                delivered = report.send_delivery(
                    to=recipients,
                    cc=[],
                    outbox=ClientReport.resolve_outbox_for_owner(owner),
                    reply_to=ClientReport.resolve_reply_to_for_owner(owner),
                )
                if delivered:
                    report.recipients = delivered
                    report.save(update_fields=["recipients"])
                    self.message_user(
                        request,
                        "Consumer report emailed to the selected recipients.",
                        messages.SUCCESS,
                    )
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

@admin.register(PackageRelease)
class PackageReleaseAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    change_list_template = "admin/core/packagerelease/change_list.html"
    list_display = (
        "version",
        "package_link",
        "severity",
        "is_current",
        "pypi_url",
        "github_url",
        "release_on",
        "revision_short",
        "published_status",
    )
    list_display_links = ("version",)
    actions = ["publish_release", "validate_releases", "test_pypi_connection"]
    change_actions = ["publish_release_action", "test_pypi_connection_action"]
    changelist_actions = ["edit_changelog", "refresh_from_pypi", "prepare_next_release"]
    readonly_fields = ("pypi_url", "github_url", "release_on", "is_current", "revision")
    fields = (
        "package",
        "release_manager",
        "version",
        "severity",
        "revision",
        "is_current",
        "pypi_url",
        "github_url",
        "release_on",
    )

    @admin.display(description="package", ordering="package")
    def package_link(self, obj):
        url = reverse("admin:core_package_change", args=[obj.package_id])
        return format_html('<a href="{}">{}</a>', url, obj.package)

    def revision_short(self, obj):
        return obj.revision_short

    revision_short.short_description = "revision"

    def edit_changelog(self, request, queryset=None):
        return redirect("admin:system-changelog-report")

    edit_changelog.label = "Edit Changelog"
    edit_changelog.short_description = "Edit Changelog"

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
        updated = 0
        restored = 0
        missing: list[str] = []

        for version, files in releases.items():
            release_on = self._release_on_from_files(files)
            release = PackageRelease.all_objects.filter(
                package=package, version=version
            ).first()
            if release:
                update_fields = []
                if release.is_deleted:
                    release.is_deleted = False
                    update_fields.append("is_deleted")
                    restored += 1
                if not release.pypi_url:
                    release.pypi_url = (
                        f"https://pypi.org/project/{package.name}/{version}/"
                    )
                    update_fields.append("pypi_url")
                if release_on and release.release_on != release_on:
                    release.release_on = release_on
                    update_fields.append("release_on")
                    updated += 1
                if update_fields:
                    release.save(update_fields=update_fields)
                continue
            missing.append(version)

        if updated or restored:
            PackageRelease.dump_fixture()
            message_parts = []
            if updated:
                message_parts.append(
                    f"Updated release date for {updated} release"
                    f"{'s' if updated != 1 else ''}"
                )
            if restored:
                message_parts.append(
                    f"Restored {restored} release{'s' if restored != 1 else ''}"
                )
            self.message_user(request, "; ".join(message_parts), messages.SUCCESS)
        elif not missing:
            self.message_user(request, "No matching releases found", messages.INFO)

        if missing:
            versions = ", ".join(sorted(missing))
            count = len(missing)
            message = (
                "Manual creation required for "
                f"{count} release{'s' if count != 1 else ''}: {versions}"
            )
            self.message_user(request, message, messages.WARNING)

    refresh_from_pypi.label = "Refresh from PyPI"
    refresh_from_pypi.short_description = "Refresh from PyPI"

    @staticmethod
    def _release_on_from_files(files):
        if not files:
            return None
        candidates = []
        for item in files:
            stamp = item.get("upload_time_iso_8601") or item.get("upload_time")
            if not stamp:
                continue
            when = parse_datetime(stamp)
            if when is None:
                continue
            if timezone.is_naive(when):
                when = timezone.make_aware(when, datetime.timezone.utc)
            candidates.append(when.astimezone(datetime.timezone.utc))
        if not candidates:
            return None
        return min(candidates)

    def prepare_next_release(self, request, queryset):
        package = Package.objects.filter(is_active=True).first()
        if not package:
            self.message_user(request, "No active package", messages.ERROR)
            return redirect("admin:core_packagerelease_changelist")
        return PackageAdmin._prepare(self, request, package)

    prepare_next_release.label = "Prepare next Release"
    prepare_next_release.short_description = "Prepare next release"

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

    def _emit_pypi_check_messages(
        self, request, release, result: release_utils.PyPICheckResult
    ) -> None:
        level_map = {
            "success": messages.SUCCESS,
            "warning": messages.WARNING,
            "error": messages.ERROR,
        }
        prefix = f"{release}: "
        for level, message in result.messages:
            self.message_user(request, prefix + message, level_map.get(level, messages.INFO))
        if result.ok:
            self.message_user(
                request,
                f"{release}: PyPI connectivity check passed",
                messages.SUCCESS,
            )

    @admin.action(description="Test PyPI connectivity")
    def test_pypi_connection(self, request, queryset):
        if not queryset:
            self.message_user(
                request,
                "Select at least one release to test",
                messages.ERROR,
            )
            return
        for release in queryset:
            result = release_utils.check_pypi_readiness(release=release)
            self._emit_pypi_check_messages(request, release, result)

    def test_pypi_connection_action(self, request, obj):
        result = release_utils.check_pypi_readiness(release=obj)
        self._emit_pypi_check_messages(request, obj, result)

    test_pypi_connection_action.label = "Test PyPI connectivity"
    test_pypi_connection_action.short_description = "Test PyPI connectivity"

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
