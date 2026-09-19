from django.contrib import admin

from apps.energy.models import CustomerAccount, EnergyTariff, LedgerEntry


@admin.register(EnergyTariff)
class EnergyTariffAdmin(admin.ModelAdmin):
    list_display = ("code", "price_mxn_per_kwh", "active")
    list_filter = ("active",)
    search_fields = ("code",)


@admin.register(CustomerAccount)
class CustomerAccountAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "balance_kwh", "active")
    list_filter = ("active",)
    search_fields = ("key", "name")


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("account", "delta_kwh", "source", "occurred_at")
    list_filter = ("source",)
    search_fields = ("external_reference",)
