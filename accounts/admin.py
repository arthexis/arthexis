from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from import_export import resources
from import_export.admin import ImportExportModelAdmin

from .models import User, RFID, Account, Vehicle


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


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("user", "credits_kwh", "total_kwh_spent", "balance_kwh")
    readonly_fields = ("balance_kwh",)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("vin", "brand", "model", "account")
