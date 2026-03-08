from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from .models import WikimediaBridge


@admin.register(WikimediaBridge)
class WikimediaBridgeAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "slug",
        "language_code",
        "api_endpoint",
        "timeout",
        "cache_timeout",
    )
    search_fields = ("name", "slug", "api_endpoint")
    list_filter = ("language_code",)
    fieldsets = (
        (None, {"fields": ("name", "slug", "language_code")}),
        (_("Connectivity"), {"fields": ("api_endpoint", "user_agent", "timeout")}),
        (_("Caching"), {"fields": ("cache_timeout",)}),
    )
