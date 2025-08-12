from django.contrib import admin
from django import forms

import asyncio

from .models import Charger, Simulator, MeterReading
from .simulator import ChargePointSimulator
from . import store


class ChargerAdminForm(forms.ModelForm):
    class Meta:
        model = Charger
        fields = "__all__"

        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "any"}),
            "longitude": forms.NumberInput(attrs={"step": "any"}),
        }

    class Media:
        css = {
            "all": ("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",)
        }
        js = (
            "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
            "ocpp/charger_map.js",
        )


@admin.register(Charger)
class ChargerAdmin(admin.ModelAdmin):
    form = ChargerAdminForm
    fieldsets = (
        (
            "General",
            {
                "fields": (
                    "charger_id",
                    "name",
                    "config",
                    "require_rfid",
                    "last_heartbeat",
                    "last_meter_values",
                    "last_path",
                )
            },
        ),
        (
            "References",
            {
                "fields": (("latitude", "longitude"), "reference"),
            },
        ),
    )
    list_display = (
        "charger_id",
        "name",
        "require_rfid",
        "latitude",
        "longitude",
        "last_heartbeat",
        "test_link",
        "log_link",
        "status_link",
    )
    search_fields = ("charger_id", "name")
    actions = ["purge_data", "delete_selected"]

    def test_link(self, obj):
        from django.utils.html import format_html

        return format_html(
            '<a href="{}" target="_blank">open</a>', obj.get_absolute_url()
        )

    test_link.short_description = "Landing Page"

    def log_link(self, obj):
        from django.utils.html import format_html
        from django.urls import reverse

        url = reverse("charger-log", args=[obj.charger_id])
        return format_html('<a href="{}" target="_blank">view</a>', url)

    log_link.short_description = "Log"
    
    def status_link(self, obj):
        from django.utils.html import format_html
        from django.urls import reverse

        url = reverse("charger-status", args=[obj.charger_id])
        return format_html('<a href="{}" target="_blank">status</a>', url)

    status_link.short_description = "Status Page"

    def purge_data(self, request, queryset):
        for charger in queryset:
            charger.purge()
        self.message_user(request, "Data purged for selected chargers")

    purge_data.short_description = "Purge data"

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            obj.delete()


@admin.register(Simulator)
class SimulatorAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "cp_path",
        "host",
        "ws_port",
        "ws_url",
        "interval",
        "kwh_max",
        "running",
        "log_link",
    )
    actions = ("start_simulator", "stop_simulator")

    def running(self, obj):
        return obj.pk in store.simulators

    running.boolean = True

    def start_simulator(self, request, queryset):
        for obj in queryset:
            if obj.pk in store.simulators:
                self.message_user(request, f"{obj.name}: already running")
                continue
            store.register_log_name(obj.cp_path, obj.name)
            sim = ChargePointSimulator(obj.as_config())
            started, status, log_file = sim.start()
            if started:
                store.simulators[obj.pk] = sim
            self.message_user(
                request, f"{obj.name}: {status}. Log: {log_file}"
            )

    start_simulator.short_description = "Start selected simulators"

    def stop_simulator(self, request, queryset):
        async def _stop(objs):
            for obj in objs:
                sim = store.simulators.pop(obj.pk, None)
                if sim:
                    await sim.stop()

        asyncio.get_event_loop().create_task(_stop(list(queryset)))
        self.message_user(request, "Stopping simulators")

    stop_simulator.short_description = "Stop selected simulators"

    def log_link(self, obj):
        from django.utils.html import format_html
        from django.urls import reverse

        url = reverse("charger-log", args=[obj.cp_path])
        return format_html('<a href="{}" target="_blank">view</a>', url)

    log_link.short_description = "Log"


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "timestamp",
        "value",
        "measurand",
        "unit",
        "connector_id",
        "transaction_id",
    )
    list_filter = ("charger", "measurand")

