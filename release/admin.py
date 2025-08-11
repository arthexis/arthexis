from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import redirect
from django.core.management import call_command
from django.utils.html import format_html

from .models import PackageConfig, TestLog, Todo, SeedData
from . import utils


@admin.register(PackageConfig)
class PackageConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "author", "repository_url")
    actions = ["build_package", "build_readme"]

    @admin.action(description="Build selected packages for PyPI")
    def build_package(self, request, queryset):
        for cfg in queryset:
            try:
                cfg.build(all=True)
                self.message_user(request, f"Built {cfg.name}", messages.SUCCESS)
            except utils.ReleaseError as exc:
                self.message_user(request, str(exc), messages.ERROR)

    @admin.action(description="Rebuild README")
    def build_readme(self, request, queryset):  # pragma: no cover - queryset unused
        call_command("build_readme")
        url = reverse("website:index")
        self.message_user(
            request,
            format_html(
                'README rebuilt. <a href="{}" target="_blank">View README</a>', url
            ),
            messages.SUCCESS,
        )


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


@admin.register(SeedData)
class SeedDataAdmin(admin.ModelAdmin):
    list_display = ("created", "auto_install")
    actions = ["install_selected"]
    change_list_template = "admin/release/seeddata/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("generate/", self.admin_site.admin_view(self.generate_seeddata), name="release_seeddata_generate"),
        ]
        return custom + urls

    def generate_seeddata(self, request):
        SeedData.create_snapshot()
        self.message_user(request, "Seed data snapshot created", messages.SUCCESS)
        return redirect("..")

    @admin.action(description="Install selected seed data")
    def install_selected(self, request, queryset):
        for seed in queryset:
            seed.install()
        self.message_user(request, f"Installed {queryset.count()} seed data sets", messages.SUCCESS)
