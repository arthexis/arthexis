"""Admin wiring for the reference plugin."""

from django.contrib import admin

from .models import SampleConnector


@admin.register(SampleConnector)
class SampleConnectorAdmin(admin.ModelAdmin):
    list_display = ("slug", "title")
    search_fields = ("slug", "title")
