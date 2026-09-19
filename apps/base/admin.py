from django.contrib import admin

from apps.base.models import SchemaGeneration


@admin.register(SchemaGeneration)
class SchemaGenerationAdmin(admin.ModelAdmin):
    list_display = ("generation", "created_at")
    readonly_fields = ("generation", "created_at")
