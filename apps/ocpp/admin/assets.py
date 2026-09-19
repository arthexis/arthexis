from django.contrib import admin

from apps.ocpp.models import (
    Charger,
    ChargerConnection,
    ChargerVariable,
    Connector,
    StationModel,
)


@admin.register(StationModel)
class StationModelAdmin(admin.ModelAdmin):
    list_display = ("vendor", "model", "preferred_protocol")
    search_fields = ("vendor", "family", "model")


@admin.register(Charger)
class ChargerAdmin(admin.ModelAdmin):
    list_display = (
        "identity",
        "station_model",
        "node",
        "authorization_mode",
        "enrolled_at",
        "active",
    )
    list_filter = ("active", "authorization_mode")
    search_fields = ("identity",)
    exclude = ("connection_token_hash",)


@admin.register(ChargerConnection)
class ChargerConnectionAdmin(admin.ModelAdmin):
    list_display = ("charger", "protocol", "connected_at")
    search_fields = ("charger__identity",)


@admin.register(Connector)
class ConnectorAdmin(admin.ModelAdmin):
    list_display = ("charger", "number", "status")
    list_filter = ("status",)


@admin.register(ChargerVariable)
class ChargerVariableAdmin(admin.ModelAdmin):
    list_display = ("charger", "component", "variable", "attribute_type", "mutable")
    list_filter = ("attribute_type", "mutable")
    search_fields = ("component", "variable")
