from django.contrib import admin

import asyncio

from .models import Charger, Simulator
from .simulator import ChargePointSimulator
from . import store


@admin.register(Charger)
class ChargerAdmin(admin.ModelAdmin):
    list_display = ("charger_id", "name", "require_rfid", "last_heartbeat")
    search_fields = ("charger_id", "name")


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
