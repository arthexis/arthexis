from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.urls import path, reverse
from django.shortcuts import redirect, render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget
from django.contrib.auth.models import Group
from django.utils.html import format_html
from .models import (
    User,
    Account,
    Vehicle,
    Credit,
    Address,
    Product,
    Subscription,
    Brand,
    WMICode,
    EVModel,
    RFID,
    Reference,
    Message,
)
from .notifications import notify


class SecurityGroup(Group):
    class Meta:
        proxy = True
        verbose_name = "Security Group"
        verbose_name_plural = "Security Groups"


admin.site.unregister(Group)


@admin.register(Reference)
class ReferenceAdmin(admin.ModelAdmin):
    list_display = ("alt_text", "content_type", "include_in_footer", "author")
    readonly_fields = ("uses", "qr_code", "author")
    fields = (
        "alt_text",
        "content_type",
        "value",
        "file",
        "method",
        "include_in_footer",
        "author",
        "uses",
        "qr_code",
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


@admin.register(SecurityGroup)
class SecurityGroupAdmin(admin.ModelAdmin):
    pass


class AccountRFIDForm(forms.ModelForm):
    """Form for assigning existing RFIDs to an account."""

    class Meta:
        model = Account.rfids.through
        fields = ["rfid"]

    def clean_rfid(self):
        rfid = self.cleaned_data["rfid"]
        if rfid.accounts.exclude(pk=self.instance.account_id).exists():
            raise forms.ValidationError("RFID is already assigned to another account")
        return rfid


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "body", "node", "created")
    search_fields = ("subject", "body")
    ordering = ("-created",)
    actions = ["send_messages"]

    @admin.action(description="Send selected messages")
    def send_messages(self, request, queryset):
        for msg in queryset:
            notify(msg.subject, msg.body)
        self.message_user(request, f"{queryset.count()} messages sent")


class AccountRFIDInline(admin.TabularInline):
    model = Account.rfids.through
    form = AccountRFIDForm
    autocomplete_fields = ["rfid"]
    extra = 0
    verbose_name = "RFID"
    verbose_name_plural = "RFIDs"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Contact", {"fields": ("phone_number", "address", "has_charger")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Contact", {"fields": ("phone_number", "address", "has_charger")}),
    )


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("street", "number", "municipality", "state", "postal_code")
    search_fields = ("street", "municipality", "postal_code")


class CreditInline(admin.TabularInline):
    model = Credit
    fields = ("amount_kw", "created_by", "created_on")
    readonly_fields = ("created_by", "created_on")
    extra = 0


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    change_list_template = "admin/core/account/change_list.html"
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
    inlines = [AccountRFIDInline, CreditInline]
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
            if isinstance(obj, Credit) and not obj.created_by:
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
                name="core_account_onboard_details",
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
            vehicle_id = forms.CharField(required=False, label="Vehicle ID")

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
                account = Account.objects.create(user=user, name=username.upper())
                rfid_val = form.cleaned_data["rfid"].upper()
                if rfid_val:
                    tag, _ = RFID.objects.get_or_create(rfid=rfid_val)
                    account.rfids.add(tag)
                vehicle_vin = form.cleaned_data["vehicle_id"]
                if vehicle_vin:
                    Vehicle.objects.create(account=account, vin=vehicle_vin)
                self.message_user(request, "Customer onboarded")
                return redirect("admin:core_account_changelist")
        else:
            form = OnboardForm()

        context = self.admin_site.each_context(request)
        context.update({"form": form})
        return render(request, "core/onboard_details.html", context)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("vin", "brand", "model", "account")


@admin.register(Credit)
class CreditAdmin(admin.ModelAdmin):
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
class BrandAdmin(admin.ModelAdmin):
    fields = ("name",)
    list_display = ("name", "wmi_codes_display")
    inlines = [WMICodeInline]

    def wmi_codes_display(self, obj):
        return ", ".join(obj.wmi_codes.values_list("code", flat=True))

    wmi_codes_display.short_description = "WMI codes"


@admin.register(EVModel)
class EVModelAdmin(admin.ModelAdmin):
    fields = ("brand", "name")
    list_display = ("name", "brand")
    list_filter = ("brand",)


admin.site.register(Product)
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
class RFIDAdmin(ImportExportModelAdmin):
    change_list_template = "admin/core/rfid/change_list.html"
    resource_class = RFIDResource
    list_display = (
        "label_id",
        "rfid",
        "color",
        "released",
        "accounts_display",
        "allowed",
        "added_on",
        "last_seen_on",
    )
    list_filter = ("color", "released", "allowed")
    search_fields = ("label_id", "rfid")
    autocomplete_fields = ["accounts"]
    raw_id_fields = ["reference"]
    actions = ["scan_rfids", "swap_color"]
    readonly_fields = ("added_on", "last_seen_on")
    form = RFIDForm

    def accounts_display(self, obj):
        return ", ".join(str(a) for a in obj.accounts.all())

    accounts_display.short_description = "Accounts"

    def scan_rfids(self, request, queryset):
        return redirect("admin:core_rfid_scan")

    scan_rfids.short_description = "Scan new RFIDs"

    def swap_color(self, request, queryset):
        for tag in queryset:
            tag.color = RFID.WHITE if tag.color == RFID.BLACK else RFID.BLACK
            tag.save()
        self.message_user(request, "RFID colors swapped")

    swap_color.short_description = "Swap color"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
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

    def scan_view(self, request):
        context = self.admin_site.each_context(request)
        context["scan_url"] = reverse("admin:core_rfid_scan_next")
        context["admin_change_url_template"] = reverse(
            "admin:core_rfid_change", args=[0]
        )
        return render(request, "admin/core/rfid/scan.html", context)

    def scan_next(self, request):
        from rfid.scanner import scan_sources

        result = scan_sources(request)
        status = 500 if result.get("error") else 200
        return JsonResponse(result, status=status)

