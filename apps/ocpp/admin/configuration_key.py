from .common_imports import *

class ConfigurationKeyAdmin(admin.ModelAdmin):
    list_display = ("configuration", "key", "position", "readonly")
    ordering = ("configuration", "position", "id")

    def get_model_perms(self, request):  # pragma: no cover - admin hook
        return {}
