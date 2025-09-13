from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.urls import path, reverse
from django.shortcuts import redirect, render
from django.http import JsonResponse, HttpResponseBase, HttpResponseRedirect
from django.template.response import TemplateResponse
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
import json
import uuid
import requests
import datetime
import calendar
from django_object_actions import DjangoObjectActions
from post_office.admin import LogAdmin as PostOfficeLogAdmin
from post_office.models import Log as PostOfficeLog
from ocpp.models import Transaction
from .models import (
    User,
    EnergyAccount,
    ElectricVehicle,
    EnergyCredit,
    EnergyReport,
    Address,
    Product,
    Subscription,
    Brand,
    WMICode,
    EVModel,
    RFID,
    SigilRoot,
    CustomSigil,
    Reference,
    OdooProfile,
    EmailInbox as CoreEmailInbox,
    EmailCollector as CoreEmailCollector,
    Package,
    PackageRelease,
    ReleaseManager,
    SecurityGroup,
    InviteLead,
    ChatProfile,
    NewsArticle,
    Todo,
)
from .user_data import EntityModelAdmin
from .widgets import OdooProductWidget


admin.site.unregister(Group)
admin.site.unregister(PostOfficeLog)


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


class WorkgroupReleaseManager(ReleaseManager):
    class Meta:
        proxy = True
        app_label = "post_office"
        verbose_name = ReleaseManager._meta.verbose_name
        verbose_name_plural = ReleaseManager._meta.verbose_name_plural


class WorkgroupSecurityGroup(SecurityGroup):
    class Meta:
        proxy = True
        app_label = "post_office"
        verbose_name = SecurityGroup._meta.verbose_name
        verbose_name_plural = SecurityGroup._meta.verbose_name_plural


class WorkgroupNewsArticle(NewsArticle):
    class Meta:
        proxy = True
        app_label = "post_office"
        verbose_name = NewsArticle._meta.verbose_name
        verbose_name_plural = NewsArticle._meta.verbose_name_plural


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


@admin.register(WorkgroupReleaseManager)
class ReleaseManagerAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    list_display = ("user", "pypi_username", "pypi_url")
    actions = ["test_credentials"]
    change_actions = ["test_credentials_action"]

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
        model = WorkgroupSecurityGroup
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


@admin.register(WorkgroupSecurityGroup)
class SecurityGroupAdmin(DjangoGroupAdmin):
    form = SecurityGroupAdminForm
    fieldsets = ((None, {"fields": ("name", "parent", "users", "permissions")}),)
    filter_horizontal = ("permissions",)


@admin.register(WorkgroupNewsArticle)
class NewsArticleAdmin(EntityModelAdmin):
    list_display = ("name", "published")
    search_fields = ("name", "content")
    ordering = ("-published",)


@admin.register(InviteLead)
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


class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Contact", {"fields": ("phone_number", "address", "has_charger")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Contact", {"fields": ("phone_number", "address", "has_charger")}),
    )


@admin.register(Address)
class AddressAdmin(EntityModelAdmin):
    change_form_template = "admin/user_datum_change_form.html"
    list_display = ("street", "number", "municipality", "state", "postal_code")
    search_fields = ("street", "municipality", "postal_code")

    def save_model(self, request, obj, form, change):
        if "_saveacopy" in request.POST:
            obj.pk = None
            super().save_model(request, obj, form, False)
        else:
            super().save_model(request, obj, form, change)


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
            return self.instance.password
        return pwd


class EmailCollector(CoreEmailCollector):
    class Meta:
        proxy = True
        app_label = "post_office"
        verbose_name = CoreEmailCollector._meta.verbose_name
        verbose_name_plural = CoreEmailCollector._meta.verbose_name_plural


class EmailCollectorInline(admin.TabularInline):
    model = CoreEmailCollector
    extra = 0


@admin.register(EmailCollector)
class EmailCollectorAdmin(EntityModelAdmin):
    list_display = ("inbox", "subject", "sender", "body", "fragment")
    search_fields = ("subject", "sender", "body", "fragment")


@admin.register(OdooProfile)
class OdooProfileAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    change_form_template = "django_object_actions/change_form.html"
    form = OdooProfileAdminForm
    list_display = ("user", "host", "database", "verified_on")
    readonly_fields = ("verified_on", "odoo_uid", "name", "email")
    actions = ["verify_credentials"]
    change_actions = ["verify_credentials_action"]
    fieldsets = (
        (None, {"fields": ("user", "host", "database", "username", "password")}),
        ("Odoo", {"fields": ("verified_on", "odoo_uid", "name", "email")}),
    )

    def _verify_credentials(self, request, profile):
        try:
            profile.verify()
            self.message_user(request, f"{profile.user} verified")
        except Exception as exc:  # pragma: no cover - admin feedback
            self.message_user(request, f"{profile.user}: {exc}", level=messages.ERROR)

    @admin.action(description="Test credentials")
    def verify_credentials(self, request, queryset):
        for profile in queryset:
            self._verify_credentials(request, profile)

    def verify_credentials_action(self, request, obj):
        self._verify_credentials(request, obj)

    verify_credentials_action.label = "Test credentials"
    verify_credentials_action.short_description = "Test credentials"


class EmailInbox(CoreEmailInbox):
    class Meta:
        proxy = True
        app_label = "post_office"
        verbose_name = CoreEmailInbox._meta.verbose_name
        verbose_name_plural = CoreEmailInbox._meta.verbose_name_plural


class EmailLog(PostOfficeLog):
    class Meta:
        proxy = True
        app_label = "post_office"
        verbose_name = "Email Log"
        verbose_name_plural = "Email Logs"


class EmailInboxAdminForm(forms.ModelForm):
    """Admin form for :class:`core.models.EmailInbox` with hidden password."""

    password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave blank to keep the current password.",
    )

    class Meta:
        model = CoreEmailInbox
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
            return self.instance.password
        return pwd


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


@admin.register(EmailLog)
class EmailLogAdmin(PostOfficeLogAdmin):
    pass


@admin.register(EmailInbox)
class EmailInboxAdmin(SaveBeforeChangeAction, EntityModelAdmin):
    form = EmailInboxAdminForm
    list_display = ("user", "username", "host", "protocol")
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
                name="post_office_emailinbox_test",
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
                "admin:post_office_emailinbox_test", args=[object_id]
            )
        return super().changeform_view(request, object_id, form_url, extra_context)

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "user",
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

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        obj.__class__ = EmailInbox

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
        }
        return TemplateResponse(request, "admin/core/emailinbox/search.html", context)


class WorkgroupChatProfile(ChatProfile):
    class Meta:
        proxy = True
        app_label = "post_office"
        verbose_name = ChatProfile._meta.verbose_name
        verbose_name_plural = ChatProfile._meta.verbose_name_plural


@admin.register(WorkgroupChatProfile)
class ChatProfileAdmin(EntityModelAdmin):
    list_display = ("user", "created_at", "last_used_at", "is_active")
    readonly_fields = ("user_key_hash",)

    change_form_template = "admin/workgroupchatprofile_change_form.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/generate-key/",
                self.admin_site.admin_view(self.generate_key),
                name="post_office_workgroupchatprofile_generate_key",
            ),
        ]
        return custom + urls

    def generate_key(self, request, object_id, *args, **kwargs):
        profile = self.get_object(request, object_id)
        if profile is None:
            return HttpResponseRedirect("../")
        profile, key = ChatProfile.issue_key(profile.user)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": profile,
            "user_key": key,
        }
        return TemplateResponse(request, "admin/chatprofile_key.html", context)


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
    fields = ("account", "vin", "license_plate", "brand", "model")


@admin.register(EnergyCredit)
class EnergyCreditAdmin(EntityModelAdmin):
    list_display = ("account", "amount_kw", "created_by", "created_on")
    readonly_fields = ("created_by", "created_on")

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


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
    list_display = ("name", "brand")
    list_filter = ("brand",)


class ProductAdminForm(forms.ModelForm):
    odoo_product = forms.JSONField(required=False, widget=OdooProductWidget)

    class Meta:
        model = Product
        fields = "__all__"


@admin.register(Product)
class ProductAdmin(EntityModelAdmin):
    form = ProductAdminForm


admin.site.register(Subscription)


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
        "color",
        "kind",
        "released",
        "energy_accounts_display",
        "allowed",
        "added_on",
        "last_seen_on",
    )
    list_filter = ("color", "released", "allowed")
    search_fields = ("label_id", "rfid")
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
        context["report"] = EnergyReport.build_rows()
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


@admin.register(EnergyReport)
class EnergyReportAdmin(EntityModelAdmin):
    list_display = ("created_on", "start_date", "end_date")
    readonly_fields = ("created_on", "data")

    change_list_template = "admin/core/energyreport/change_list.html"

    class EnergyReportForm(forms.Form):
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
                name="core_energyreport_generate",
            ),
        ]
        return custom + urls

    def generate_view(self, request):
        form = self.EnergyReportForm(request.POST or None)
        report = None
        if request.method == "POST" and form.is_valid():
            report = EnergyReport.generate(
                form.cleaned_data["start"], form.cleaned_data["end"]
            )
        context = self.admin_site.each_context(request)
        context.update({"form": form, "report": report})
        return TemplateResponse(
            request, "admin/core/energyreport/generate.html", context
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
    list_display = ("description", "url")
