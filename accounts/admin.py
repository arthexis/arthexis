from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from import_export import resources
from import_export.admin import ImportExportModelAdmin

from .models import User, RFID, Account, Vehicle, Credit


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + ((None, {"fields": ("phone_number",)}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + ((None, {"fields": ("phone_number",)}),)



class RFIDResource(resources.ModelResource):
    class Meta:
        model = RFID
        fields = ("rfid", "user__username", "allowed")


@admin.register(RFID)
class RFIDAdmin(ImportExportModelAdmin):
    resource_class = RFIDResource
    list_display = ("rfid", "user", "allowed", "added_on")


class CreditInline(admin.TabularInline):
    model = Credit
    fields = ("amount_kwh", "created_by", "created_on")
    readonly_fields = ("created_by", "created_on")
    extra = 0


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("user", "credits_kwh", "total_kwh_spent", "balance_kwh")
    readonly_fields = ("credits_kwh", "total_kwh_spent", "balance_kwh")
    inlines = [CreditInline]

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
