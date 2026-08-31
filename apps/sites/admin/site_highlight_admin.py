from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.sites.models import SiteHighlight


@admin.register(SiteHighlight)
class SiteHighlightAdmin(admin.ModelAdmin):
    """Admin for public-site highlight messages."""

    date_hierarchy = "highlight_date"
    list_display = ("title", "highlight_date", "is_enabled", "updated_at", "created_at")
    list_filter = ("is_enabled", "highlight_date")
    ordering = ("-highlight_date", "-updated_at", "-created_at")
    search_fields = ("title", "story")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "title",
                    "highlight_date",
                    "story",
                    "is_enabled",
                )
            },
        ),
        (
            _("Audit"),
            {
                "fields": ("updated_at", "created_at"),
            },
        ),
    )
    readonly_fields = ("updated_at", "created_at")
