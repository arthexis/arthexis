from django.contrib import admin

from apps.sigils.models import SigilRoot


@admin.register(SigilRoot)
class SigilRootAdmin(admin.ModelAdmin):
    list_display = ("prefix", "context_type", "user_safe", "active")
    list_filter = ("user_safe", "active")
    search_fields = ("prefix", "context_type")
