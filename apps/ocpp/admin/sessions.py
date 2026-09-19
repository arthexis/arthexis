from django.contrib import admin

from apps.ocpp.models import MeterValue, OcppTransaction, Reservation


@admin.register(OcppTransaction)
class OcppTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "remote_id",
        "charger",
        "id_tag",
        "started_at",
        "stopped_at",
        "energy_kwh",
    )
    search_fields = ("remote_id", "id_tag")


@admin.register(MeterValue)
class MeterValueAdmin(admin.ModelAdmin):
    list_display = (
        "transaction",
        "sampled_at",
        "value",
        "unit",
        "multiplier",
        "measurand",
    )
    list_filter = ("measurand",)


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("remote_id", "charger", "id_tag", "expires_at", "status")
    list_filter = ("status",)
    search_fields = ("remote_id", "id_tag")
