from __future__ import annotations

from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.features.models import Feature
from apps.features.parameters import (
    get_feature_parameter,
    set_feature_parameter_values,
)

from .constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
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
    timeout_seconds = forms.ChoiceField(
        label=_("Model timeout"),
        required=False,
        choices=(("60", "60"), ("120", "120"), ("180", "180"), ("240", "240"), ("300", "300"), ("600", "600")),
        initial="240",
        help_text=_("Timeout (seconds) used to invoke the local model command."),
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
                "timeout_seconds": get_feature_parameter(
                    LLM_SUMMARY_SUITE_FEATURE_SLUG,
                    "timeout_seconds",
                    fallback="240",
                ),
            },
        )

        if request.method == "POST" and form.is_valid():
            model_choice = form.cleaned_data["model_choice"]
            model_command = (form.cleaned_data.get("model_command") or "").strip()
            timeout_seconds = (form.cleaned_data.get("timeout_seconds") or "240").strip()
            if model_choice == LLMSummaryWizardForm.MODEL_DEFAULT:
                config.model_path = ""
            else:
                config.model_path = form.cleaned_data.get("model_path", "").strip()
            config.model_command = model_command
            if form.cleaned_data.get("install_model"):
                model_dir = ensure_local_model(
                    config,
                    preferred_path=(
                        config.model_path
                        if model_choice == LLMSummaryWizardForm.MODEL_CUSTOM
                        else None
                    ),
                )
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

            suite_feature, _created = Feature.objects.get_or_create(
                slug=LLM_SUMMARY_SUITE_FEATURE_SLUG,
                defaults={
                    "display": "LLM Summary Suite",
                    "source": Feature.Source.CUSTOM,
                    "is_enabled": True,
                    "node_feature": None,
                },
            )
            set_feature_parameter_values(
                suite_feature,
                {
                    "model_path": config.model_path,
                    "model_command": model_command,
                    "timeout_seconds": timeout_seconds,
                },
            )
            suite_feature.save(update_fields=["metadata", "updated_at"])
            messages.success(request, _("LLM summary settings updated."))
            return redirect(
                reverse("admin:summary_llmsummaryconfig_change", args=[config.pk])
            )

        checks = self._build_setup_checks(config, resolved_path=resolved_path)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "form": form,
            "config": config,
            "resolved_path": resolved_path,
            "title": _("LLM Summary Model Wizard"),
            "breadcrumbs_title": _("LLM Summary Model Wizard"),
            "checks": checks,
            "change_url": reverse(
                "admin:summary_llmsummaryconfig_change", args=[config.pk]
            ),
            "changelist_url": reverse("admin:summary_llmsummaryconfig_changelist"),
        }
        return TemplateResponse(
            request, "admin/summary/llm_summary_wizard.html", context
        )

    def _build_setup_checks(self, config: LLMSummaryConfig, *, resolved_path: Path) -> list[dict[str, str]]:
        """Return operator-facing setup checklist rows for the wizard."""

        from apps.nodes.models import Node
        from apps.summary.node_features import get_llm_summary_prereq_state

        checks: list[dict[str, str]] = []
        suite_feature = Feature.objects.filter(slug=LLM_SUMMARY_SUITE_FEATURE_SLUG).first()
        suite_enabled = bool(suite_feature and suite_feature.is_enabled)
        checks.append({
            "label": _("Suite feature enabled"),
            "status": _("Ready") if suite_enabled else _("Missing"),
            "detail": _(
                "Enable the LLM Summary suite feature in Suite Features to allow automation."
            ),
        })

        node = Node.get_local()
        if node is None:
            checks.append({
                "label": _("Local node registration"),
                "status": _("Missing"),
                "detail": _("Register this host as a local node before enabling summaries."),
            })
            return checks

        checks.append({
            "label": _("Node feature assignment"),
            "status": _("Ready") if node.has_feature("llm-summary") else _("Missing"),
            "detail": _("Assign the LLM Summary node feature to this node."),
        })

        prereqs = get_llm_summary_prereq_state(
            base_dir=Path(settings.BASE_DIR),
            base_path=node.get_base_path(),
        )
        checks.append({
            "label": _("LCD lock"),
            "status": _("Ready") if prereqs["lcd_enabled"] else _("Missing"),
            "detail": _("Enable the LCD Screen node feature and runtime lock."),
        })
        checks.append({
            "label": _("Celery lock"),
            "status": _("Ready") if prereqs["celery_enabled"] else _("Missing"),
            "detail": _("Enable the Celery Queue node feature and lock file."),
        })
        checks.append({
            "label": _("Summary configuration active"),
            "status": _("Ready") if config.is_active else _("Missing"),
            "detail": _("Activate LLM Summary Config to permit runtime generation."),
        })
        checks.append({
            "label": _("Model directory"),
            "status": _("Ready") if resolved_path.exists() else _("Missing"),
            "detail": _("Use Save and install to create the model placeholder directory."),
        })
        checks.append({
            "label": _("Reviewed"),
            "status": timezone.now().strftime("%Y-%m-%d %H:%M"),
            "detail": "",
        })
        return checks
