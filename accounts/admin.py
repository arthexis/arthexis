from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User, BlacklistedRFID, Account, Vehicle


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("RFID", {"fields": ("rfid_uid",)}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("RFID", {"fields": ("rfid_uid",)}),
    )


@admin.register(BlacklistedRFID)
class BlacklistedRFIDAdmin(admin.ModelAdmin):
    list_display = ("uid", "added_on")


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("user", "credits_kwh", "total_kwh_spent", "balance_kwh")
    readonly_fields = ("balance_kwh",)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("vin", "brand", "model", "account")
