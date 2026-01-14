from django.contrib import admin, messages
from django.utils import timezone

from .models import Thermometer
from .thermometers import read_w1_temperature


@admin.register(Thermometer)
class ThermometerAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "last_reading", "last_read_at", "is_active")
    search_fields = ("name", "slug")
    list_filter = ("is_active",)
    actions = ("sample_selected_thermometers",)

    @admin.action(description="Sample selected thermometers")
    def sample_selected_thermometers(self, request, queryset):
        reading = read_w1_temperature()
        if reading is None:
            self.message_user(
                request,
                "No thermometer reading available.",
                level=messages.WARNING,
            )
            return
        sampled_at = timezone.now()
        updated = queryset.update(last_reading=reading, last_read_at=sampled_at)
        if updated:
            self.message_user(
                request,
                f"Sampled {updated} thermometer(s).",
                level=messages.SUCCESS,
            )
