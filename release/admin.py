from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.shortcuts import render

from .models import PackageConfig
from . import utils


@admin.register(PackageConfig)
class PackageConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "author", "repository_url")
    actions = ["build_package"]

    class BuildOptionsForm(forms.Form):
        bump = forms.BooleanField(required=False, label="Bump version")
        dist = forms.BooleanField(required=False, label="Build distribution")
        twine = forms.BooleanField(required=False, label="Upload to PyPI")
        git = forms.BooleanField(required=False, label="Commit to Git")
        tag = forms.BooleanField(required=False, label="Tag release")
        force = forms.BooleanField(required=False, label="Force upload")
        all = forms.BooleanField(required=False, label="Run all steps")

    @admin.action(description="Build selected packages for PyPI")
    def build_package(self, request, queryset):
        if "apply" in request.POST:
            form = self.BuildOptionsForm(request.POST)
            if form.is_valid():
                opts = form.cleaned_data
                for cfg in queryset:
                    try:
                        cfg.build(**opts)
                        self.message_user(
                            request, f"Built {cfg.name}", messages.SUCCESS
                        )
                    except utils.ReleaseError as exc:
                        self.message_user(request, str(exc), messages.ERROR)
                return None
        else:
            form = self.BuildOptionsForm()

        context = {
            "form": form,
            "packages": queryset,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
        }
        return render(request, "release/build_options.html", context)
