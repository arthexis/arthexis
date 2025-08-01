from django.contrib import admin, messages

from .models import PackageConfig
from . import utils


@admin.register(PackageConfig)
class PackageConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "author", "repository_url")
    actions = ["build_package"]

    @admin.action(description="Build selected packages for PyPI")
    def build_package(self, request, queryset):
        for cfg in queryset:
            try:
                cfg.build(all=True)
                self.message_user(request, f"Built {cfg.name}", messages.SUCCESS)
            except utils.ReleaseError as exc:
                self.message_user(request, str(exc), messages.ERROR)
