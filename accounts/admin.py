from django import forms
from django.contrib import admin
from django.urls import path, reverse
from django.shortcuts import redirect, render
from django.http import JsonResponse
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.html import format_html
from import_export import resources
from import_export.admin import ImportExportModelAdmin

from .models import (
    UserProxy,
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
    RFIDSource,
)


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


class AccountRFIDInline(admin.TabularInline):
    model = Account.rfids.through
    form = AccountRFIDForm
    autocomplete_fields = ["rfid"]
    extra = 0
    verbose_name = "RFID"
    verbose_name_plural = "RFIDs"


@admin.register(UserProxy)
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
    fields = ("amount_kwh", "created_by", "created_on")
    readonly_fields = ("created_by", "created_on")
    extra = 0


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    change_list_template = "admin/accounts/account/change_list.html"
    list_display = (
        "name",
        "user",
        "credits_kwh",
        "total_kwh_spent",
        "balance_kwh",
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
        "credits_kwh",
        "total_kwh_spent",
        "balance_kwh",
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
                    ("credits_kwh", "total_kwh_spent", "balance_kwh"),
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
                name="accounts_account_onboard_details",
            ),
        ]
        return custom + urls

    def onboard_details(self, request):
        class OnboardForm(forms.Form):
            first_name = forms.CharField(label="First name")
            last_name = forms.CharField(label="Last name")
            rfid = forms.CharField(max_length=8, required=False, label="RFID")
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
                return redirect("admin:accounts_account_changelist")
        else:
            form = OnboardForm()

        context = self.admin_site.each_context(request)
        context.update({"form": form})
        return render(request, "accounts/onboard_details.html", context)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("vin", "brand", "model", "account")


@admin.register(Credit)
class CreditAdmin(admin.ModelAdmin):
    list_display = ("account", "amount_kwh", "created_by", "created_on")
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
    list_display = ("name",)
    inlines = [WMICodeInline]


@admin.register(EVModel)
class EVModelAdmin(admin.ModelAdmin):
    fields = ("brand", "name")
    list_display = ("name", "brand")
    list_filter = ("brand",)


admin.site.register(Product)
admin.site.register(Subscription)


class RFIDResource(resources.ModelResource):
    class Meta:
        model = RFID
        fields = ("rfid", "allowed")
        import_id_fields = ("rfid",)


@admin.register(RFID)
class RFIDAdmin(ImportExportModelAdmin):
    change_list_template = "admin/accounts/rfid/change_list.html"
    change_form_template = "admin/accounts/rfid/change_form.html"
    resource_class = RFIDResource
    list_display = ("rfid", "accounts_display", "allowed", "added_on", "write_link")
    search_fields = ("rfid",)
    autocomplete_fields = ["accounts"]
    actions = ["scan_rfids"]

    def accounts_display(self, obj):
        return ", ".join(str(a) for a in obj.accounts.all())

    accounts_display.short_description = "Accounts"

    def scan_rfids(self, request, queryset):
        return redirect("admin:accounts_rfid_scan")

    scan_rfids.short_description = "Scan new RFIDs"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "scan/",
                self.admin_site.admin_view(self.scan_view),
                name="accounts_rfid_scan",
            ),
            path(
                "scan/next/",
                self.admin_site.admin_view(self.scan_next),
                name="accounts_rfid_scan_next",
            ),
            path(
                "<slug:pk>/write/",
                self.admin_site.admin_view(self.write_view),
                name="accounts_rfid_write",
            ),
            path(
                "<slug:pk>/write/next/",
                self.admin_site.admin_view(self.write_next),
                name="accounts_rfid_write_next",
            ),
        ]
        return custom + urls

    def scan_view(self, request):
        context = self.admin_site.each_context(request)
        return render(request, "admin/accounts/rfid/scan.html", context)

    def scan_next(self, request):
        try:
            from mfrc522 import MFRC522
        except Exception as exc:  # pragma: no cover - hardware dependent
            return JsonResponse({"error": str(exc)}, status=500)

        import time
        try:
            import RPi.GPIO as GPIO  # pragma: no cover - hardware dependent
        except Exception:  # pragma: no cover - hardware dependent
            GPIO = None

        mfrc = MFRC522()
        timeout = time.time() + 1
        try:
            while time.time() < timeout:  # pragma: no cover - hardware loop
                (status, _TagType) = mfrc.MFRC522_Request(mfrc.PICC_REQIDL)
                if status == mfrc.MI_OK:
                    (status, uid) = mfrc.MFRC522_Anticoll()
                    if status == mfrc.MI_OK:
                        rfid = "".join(f"{x:02X}" for x in uid[:5])
                        tag, created = RFID.objects.get_or_create(rfid=rfid)
                        return JsonResponse({"rfid": rfid, "created": created})
                time.sleep(0.2)
            return JsonResponse({"rfid": None})
        finally:  # pragma: no cover - cleanup hardware
            if GPIO:
                try:
                    GPIO.cleanup()
                except Exception:
                    pass

    def write_link(self, obj):
        url = reverse("admin:accounts_rfid_write", args=[obj.pk])
        return format_html('<a href="{}">Write</a>', url)

    write_link.short_description = "Write"

    def write_view(self, request, pk):
        tag = RFID.objects.get(pk=pk)
        context = self.admin_site.each_context(request)
        context.update({"rfid": tag})
        return render(request, "admin/accounts/rfid/write.html", context)

    def write_next(self, request, pk):
        try:
            from mfrc522 import MFRC522
        except Exception as exc:  # pragma: no cover - hardware dependent
            return JsonResponse({"error": str(exc)}, status=500)

        import time
        try:
            import RPi.GPIO as GPIO  # pragma: no cover - hardware dependent
        except Exception:  # pragma: no cover - hardware dependent
            GPIO = None

        tag = RFID.objects.get(pk=pk)
        mfrc = MFRC522()
        timeout = time.time() + 1
        try:
            while time.time() < timeout:  # pragma: no cover - hardware loop
                (status, _TagType) = mfrc.MFRC522_Request(mfrc.PICC_REQIDL)
                if status == mfrc.MI_OK:
                    (status, uid) = mfrc.MFRC522_Anticoll()
                    if status == mfrc.MI_OK:
                        rfid = "".join(f"{x:02X}" for x in uid[:5])
                        if rfid != tag.rfid:
                            return JsonResponse({"rfid": rfid, "match": False})
                        try:
                            mfrc.MFRC522_SelectTag(uid)
                            # Default auth with factory keys, sector 1 trailer block
                            default_key = [0xFF] * 6
                            block = 7
                            if (
                                mfrc.MFRC522_Auth(
                                    mfrc.PICC_AUTHENT1A, block, default_key, uid
                                )
                                == mfrc.MI_OK
                            ):
                                key_a = [
                                    int(tag.key_a[i : i + 2], 16)
                                    for i in range(0, 12, 2)
                                ]
                                key_b = [
                                    int(tag.key_b[i : i + 2], 16)
                                    for i in range(0, 12, 2)
                                ]
                                data = key_a + [0xFF, 0x07, 0x80, 0x69] + key_b
                                mfrc.MFRC522_Write(block, data)
                                mfrc.MFRC522_StopCrypto1()
                                return JsonResponse(
                                    {"rfid": rfid, "match": True, "written": True}
                                )
                            return JsonResponse(
                                {
                                    "rfid": rfid,
                                    "match": True,
                                    "written": False,
                                    "error": "auth failed",
                                }
                            )
                        except Exception as exc:  # pragma: no cover - hardware dependent
                            return JsonResponse(
                                {
                                    "rfid": rfid,
                                    "match": True,
                                    "written": False,
                                    "error": str(exc),
                                }
                            )
                time.sleep(0.2)
            return JsonResponse({"rfid": None})
        finally:  # pragma: no cover - cleanup hardware
            if GPIO:
                try:
                    GPIO.cleanup()
                except Exception:
                    pass


@admin.register(RFIDSource)
class RFIDSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "endpoint", "is_source", "is_target")
