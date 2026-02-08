"""Admin registrations for simulator scheduling."""

from django.contrib import admin

from .models import SimulatorSchedule


@admin.register(SimulatorSchedule)
class SimulatorScheduleAdmin(admin.ModelAdmin):
    """Admin configuration for simulator schedules."""

    list_display = (
        "name",
        "simulator",
        "schedule_date",
        "start_time",
        "end_time",
        "run_count",
        "randomize",
        "active",
    )
    list_filter = ("active", "randomize", "schedule_date")
    search_fields = ("name", "simulator__name", "simulator__cp_path")
