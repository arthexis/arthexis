from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from import_export import resources
from import_export.admin import ImportExportModelAdmin

from .models import UserProxy, RFID, Account, Vehicle, Credit, Address


class AccountRFIDForm(forms.ModelForm):
    """Simple text input for assigning RFIDs to an account."""

    rfid = forms.CharField(max_length=8, label="RFID")

    class Meta:
        model = Account.rfids.through
        fields = ["rfid"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and getattr(self.instance, "rfid_id", None):
            self.fields["rfid"].initial = self.instance.rfid.rfid

    def clean_rfid(self):
        value = self.cleaned_data["rfid"].strip().upper()
        if not RFID._meta.get_field("rfid").validators[0].regex.match(value):
            raise forms.ValidationError("RFID must be 8 hexadecimal digits")
        return value

    def save(self, commit=True):
        value = self.cleaned_data["rfid"]
        rfid_obj, _ = RFID.objects.get_or_create(rfid=value)
        self.instance.rfid = rfid_obj
        return super().save(commit)


class AccountRFIDInline(admin.TabularInline):
    model = Account.rfids.through
    form = AccountRFIDForm
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



class RFIDResource(resources.ModelResource):
    class Meta:
        model = RFID
        fields = ("rfid", "allowed")


@admin.register(RFID)
class RFIDAdmin(ImportExportModelAdmin):
    resource_class = RFIDResource
    list_display = ("rfid", "accounts_display", "allowed", "added_on")

    def accounts_display(self, obj):
        return ", ".join(str(a) for a in obj.accounts.all())

    accounts_display.short_description = "Accounts"


class CreditInline(admin.TabularInline):
    model = Credit
    fields = ("amount_kwh", "created_by", "created_on")
    readonly_fields = ("created_by", "created_on")
    extra = 0


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "credits_kwh",
        "total_kwh_spent",
        "balance_kwh",
        "service_account",
        "authorized",
    )
    filter_horizontal = ("rfids",)
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
                    "user",
                    ("service_account", "authorized"),
                    ("credits_kwh", "total_kwh_spent", "balance_kwh"),
                  ]
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
