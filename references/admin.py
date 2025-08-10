from django.contrib import admin

from .models import Reference


@admin.register(Reference)
class ReferenceAdmin(admin.ModelAdmin):
    list_display = ("value", "include_in_footer", "is_seed_data")
