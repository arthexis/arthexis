from __future__ import annotations

from pathlib import Path

from django import forms
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from .models import LLMSummaryConfig
from .services import (
    DEFAULT_MODEL_DIR,
    ensure_local_model,
    get_summary_config,
    resolve_model_path,
)


class LLMSummaryWizardForm(forms.Form):
    MODEL_DEFAULT = "default"
    MODEL_CUSTOM = "custom"

    MODEL_CHOICES = (
        (MODEL_DEFAULT, _("Use the default model directory")),
        (MODEL_CUSTOM, _("Specify a custom model directory")),
    )

    model_choice = forms.ChoiceField(
        label=_("Model location"),
        choices=MODEL_CHOICES,
        initial=MODEL_DEFAULT,
        widget=forms.RadioSelect,
    )
    model_path = forms.CharField(
        label=_("Model path"),
        required=False,
        help_text=_("Directory that contains the local LLM model files."),
    )
    model_command = forms.CharField(
        label=_("Model command"),
        required=False,
        help_text=_("Optional command used to invoke the local model."),
    )
    install_model = forms.BooleanField(
        label=_("Create the model directory now"),
        required=False,
        initial=True,
        help_text=_("Creates the folder and placeholder if missing."),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("model_choice") == self.MODEL_CUSTOM:
            model_path = (cleaned.get("model_path") or "").strip()
            if not model_path:
                self.add_error("model_path", _("Enter a model path."))
            else:
                cleaned["model_path"] = model_path
        return cleaned


@admin.register(LLMSummaryConfig)
class LLMSummaryConfigAdmin(admin.ModelAdmin):
    list_display = ("display", "slug", "is_active", "installed_at", "last_run_at")
    list_filter = ("is_active",)
    search_fields = ("slug", "display")
    readonly_fields = ("installed_at", "last_run_at", "created_at", "updated_at")
    change_list_template = "admin/summary/llmsummaryconfig/change_list.html"

    def get_urls(self):
        custom = [
            path(
                "wizard/",
                self.admin_site.admin_view(self.model_wizard_view),
                name="summary_llmsummaryconfig_wizard",
            ),
        ]
        return custom + super().get_urls()

    def model_wizard_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_change_permission(request):
            messages.error(
                request, _("You do not have permission to configure LLM summaries.")
            )
            return redirect("admin:index")

        config = get_summary_config()
        resolved_path = resolve_model_path(config)
        initial_choice = (
            LLMSummaryWizardForm.MODEL_DEFAULT
            if not config.model_path or Path(config.model_path) == DEFAULT_MODEL_DIR
            else LLMSummaryWizardForm.MODEL_CUSTOM
        )
        form = LLMSummaryWizardForm(
            request.POST or None,
            initial={
                "model_choice": initial_choice,
                "model_path": config.model_path or str(resolved_path),
                "model_command": config.model_command,
            },
        )

        if request.method == "POST" and form.is_valid():
            model_choice = form.cleaned_data["model_choice"]
            model_command = (form.cleaned_data.get("model_command") or "").strip()
            if model_choice == LLMSummaryWizardForm.MODEL_DEFAULT:
                config.model_path = ""
            else:
                config.model_path = form.cleaned_data.get("model_path", "").strip()
            config.model_command = model_command
            if form.cleaned_data.get("install_model"):
                model_dir = ensure_local_model(config)
                if model_choice == LLMSummaryWizardForm.MODEL_DEFAULT:
                    config.model_path = ""
                installed_message = _("Model directory is ready at %(path)s.") % {
                    "path": str(model_dir),
                }
                messages.success(request, installed_message)
            config.save(
                update_fields=[
                    "model_path",
                    "model_command",
                    "installed_at",
                    "updated_at",
                ]
            )
            messages.success(request, _("LLM summary settings updated."))
            return redirect(
                reverse("admin:summary_llmsummaryconfig_change", args=[config.pk])
            )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "form": form,
            "config": config,
            "resolved_path": resolved_path,
            "title": _("LLM Summary Model Wizard"),
            "breadcrumbs_title": _("LLM Summary Model Wizard"),
            "change_url": reverse(
                "admin:summary_llmsummaryconfig_change", args=[config.pk]
            ),
            "changelist_url": reverse("admin:summary_llmsummaryconfig_changelist"),
        }
        return TemplateResponse(
            request, "admin/summary/llm_summary_wizard.html", context
        )
