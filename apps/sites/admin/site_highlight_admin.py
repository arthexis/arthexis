from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.sites.models import SiteHighlight


@admin.register(SiteHighlight)
class SiteHighlightAdmin(admin.ModelAdmin):
    """Admin for public-site highlight messages."""

    date_hierarchy = "highlight_date"
    list_display = ("title", "highlight_date", "is_enabled", "created_at")
    list_filter = ("is_enabled", "highlight_date")
    ordering = ("-highlight_date", "-created_at")
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
                "fields": ("created_at",),
            },
        ),
    )
    readonly_fields = ("created_at",)
