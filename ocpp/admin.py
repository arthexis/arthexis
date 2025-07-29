from django.contrib import admin
from django import forms

import asyncio

from .models import Charger, Simulator
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
    list_display = (
        "charger_id",
        "name",
        "require_rfid",
        "latitude",
        "longitude",
        "last_heartbeat",
        "test_link",
    )
    search_fields = ("charger_id", "name")

    def test_link(self, obj):
        from django.utils.html import format_html

        return format_html(
            '<a href="{}" target="_blank">open</a>', obj.get_absolute_url()
        )

    test_link.short_description = "Landing Page"


@admin.register(Simulator)
class SimulatorAdmin(admin.ModelAdmin):
    list_display = ("name", "cp_path", "host", "ws_port", "running")
    actions = ("start_simulator", "stop_simulator")

    def running(self, obj):
        return obj.pk in store.simulators

    running.boolean = True

    def start_simulator(self, request, queryset):
        for obj in queryset:
            if obj.pk in store.simulators:
                continue
            sim = ChargePointSimulator(obj.as_config())
            sim.start()
            store.simulators[obj.pk] = sim
        self.message_user(request, "Simulators started")

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
