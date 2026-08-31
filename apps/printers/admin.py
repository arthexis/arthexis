from django.contrib import admin

from .models import PrintersItem


@admin.register(PrintersItem)
class PrintersItemAdmin(admin.ModelAdmin):
    """Starter admin registration for generated model."""

    list_display = ("name", "created_at")
    search_fields = ("name",)
