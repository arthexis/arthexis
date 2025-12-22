from django.contrib import admin

from .models import Thermometer


@admin.register(Thermometer)
class ThermometerAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "last_reading", "last_read_at", "is_active")
    search_fields = ("name", "slug")
    list_filter = ("is_active",)
