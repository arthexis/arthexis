from django.contrib import admin

from .models import Charger


@admin.register(Charger)
class ChargerAdmin(admin.ModelAdmin):
    list_display = ("charger_id", "name", "require_rfid")
    search_fields = ("charger_id", "name")
