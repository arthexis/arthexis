from django.contrib import admin, messages
from django import forms
from django.template.response import TemplateResponse
from django.core.exceptions import ValidationError
from pathlib import Path

from .models import PackageRelease, TestLog, Todo
from . import utils


class BuildReleaseForm(forms.Form):
    bump = forms.BooleanField(
        initial=True, required=False, label="Bump version"
    )
    upload = forms.BooleanField(
        initial=False, required=False, label="Upload to PyPI"
    )
    tests = forms.BooleanField(
        initial=True, required=False, label="Run tests"
    )
    stash = forms.BooleanField(
        initial=False, required=False, label="Auto stash before building"
    )

    def __init__(self, *args, **kwargs):
        current_version = kwargs.pop("current_version", "")
        super().__init__(*args, **kwargs)
        if current_version:
            self.fields["bump"].help_text = f"Current version: {current_version}"


@admin.register(PackageRelease)
class PackageReleaseAdmin(admin.ModelAdmin):
    list_display = ("name", "author", "repository_url")
    actions = ["build_release"]

    @admin.action(description="Build selected packages")
    def build_release(self, request, queryset):
        if "apply" in request.POST:
            form = BuildReleaseForm(request.POST)
            if form.is_valid():
                bump = form.cleaned_data["bump"]
                upload = form.cleaned_data["upload"]
                tests_opt = form.cleaned_data["tests"]
                stash_opt = form.cleaned_data["stash"]
                for cfg in queryset:
                    try:
                        cfg.full_clean()
                        cfg.build(
                            bump=bump,
                            tests=tests_opt,
                            dist=True,
                            twine=upload,
                            git=True,
                            tag=True,
                            stash=stash_opt,
                        )
                        self.message_user(
                            request, f"Built {cfg.name}", messages.SUCCESS
                        )
                    except ValidationError as exc:
                        self.message_user(
                            request, "; ".join(exc.messages), messages.ERROR
                        )
                    except Exception as exc:
                        self.message_user(request, str(exc), messages.ERROR)
                return None
        else:
            version = (
                Path("VERSION").read_text().strip()
                if Path("VERSION").exists()
                else ""
            )
            form = BuildReleaseForm(current_version=version)
        context = {
            "form": form,
            "queryset": queryset,
            "action": "build_release",
        }
        return TemplateResponse(
            request, "admin/release/packagerelease/build_release.html", context
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




