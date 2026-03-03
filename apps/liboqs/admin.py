"""Admin registrations for liboqs models."""

from django.contrib import admin

from .models import LiboqsProfile


@admin.register(LiboqsProfile)
class LiboqsProfileAdmin(admin.ModelAdmin):
    """Admin interface for liboqs profiles."""

    list_display = (
        "display_name",
        "slug",
        "kem_algorithm",
        "signature_algorithm",
        "enabled",
        "updated_at",
    )
    list_filter = ("enabled", "kem_algorithm")
    search_fields = ("display_name", "slug", "kem_algorithm", "signature_algorithm")
