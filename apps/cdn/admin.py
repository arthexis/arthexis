"""Admin registration for CDN configuration models."""

from django.contrib import admin

from apps.locals.entity import EntityModelAdmin

from .models import CDNConfiguration


@admin.register(CDNConfiguration)
class CDNConfigurationAdmin(EntityModelAdmin):
    """Admin controls for CDN endpoint records."""

    list_display = ("name", "provider", "base_url", "is_enabled")
    list_filter = ("provider", "is_enabled")
    search_fields = ("name", "base_url", "aws_distribution_id")
