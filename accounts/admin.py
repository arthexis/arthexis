from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User, RFID, Account, Vehicle


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    pass



@admin.register(RFID)
class RFIDAdmin(admin.ModelAdmin):
    list_display = ("rfid", "user", "allowed", "added_on")


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("user", "credits_kwh", "total_kwh_spent", "balance_kwh")
    readonly_fields = ("balance_kwh",)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("vin", "brand", "model", "account")
