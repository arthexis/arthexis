"""Admin registration for liboqs models."""

from django.contrib import admin

from apps.liboqs.models import OqsAlgorithm


@admin.register(OqsAlgorithm)
class OqsAlgorithmAdmin(admin.ModelAdmin):
    """Admin listing for persisted liboqs algorithms."""

    list_display = ("name", "algorithm_type", "enabled", "discovered_at")
    list_filter = ("algorithm_type", "enabled")
    search_fields = ("name",)
    readonly_fields = ("discovered_at",)
