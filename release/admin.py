from django.contrib import admin, messages

from .models import PackageConfig, TestLog, Todo
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


@admin.register(TestLog)
class TestLogAdmin(admin.ModelAdmin):
    list_display = ("created", "status", "short_output")
    actions = ["purge_logs"]

    def short_output(self, obj):
        return (obj.output[:50] + "...") if len(obj.output) > 50 else obj.output

    @admin.action(description="Purge selected logs")
    def purge_logs(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(request, f"Purged {count} logs", messages.SUCCESS)


@admin.register(Todo)
class TodoAdmin(admin.ModelAdmin):
    list_display = ("text", "completed", "file_path", "line_number")
    list_filter = ("completed",)
