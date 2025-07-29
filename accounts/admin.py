from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User, RFID, Account


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    pass



@admin.register(RFID)
class RFIDAdmin(admin.ModelAdmin):
    list_display = ("uid", "user", "blacklisted", "added_on")


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("user", "credits_kwh", "total_kwh_spent", "balance_kwh")
    readonly_fields = ("balance_kwh",)
