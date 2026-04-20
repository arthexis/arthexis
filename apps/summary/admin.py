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

from .catalog import SUMMARY_MODEL_CHOICES
from .constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
from .models import LLMSummaryConfig
from .services import (
    build_summary_runtime_launch_plan,
    get_selected_summary_model,
    get_summary_config,
    probe_summary_runtime,
    resolve_model_path,
    resolve_runtime_base_url,
    resolve_runtime_binary_path,
    summary_runtime_service_lock_enabled,
    sync_summary_suite_feature,
    summary_runtime_is_ready,
)


class LLMSummaryWizardForm(forms.Form):
    """Collect safe local summary settings for operators."""

    selected_model = forms.ChoiceField(
        label=_("Summary model"),
        choices=(("", _("Select a model")), *SUMMARY_MODEL_CHOICES),
        required=False,
        help_text=_("Pick one built-in model profile to bind to the LCD summary runtime."),
    )
    model_path = forms.CharField(
        label=_("Model path"),
        required=False,
        help_text=_("Optional local artifact path or cache directory for the selected model."),
    )
    backend = forms.ChoiceField(
        label=_("Summary backend"),
        choices=LLMSummaryConfig.Backend.choices,
        initial=LLMSummaryConfig.Backend.LLAMA_CPP_SERVER,
        help_text=_("Use a local llama.cpp server so the summary task runs against a real loaded model."),
    )
    runtime_base_url = forms.CharField(
        label=_("Runtime base URL"),
        required=False,
        initial="http://127.0.0.1:8080/v1",
        help_text=_("OpenAI-compatible base URL exposed by the local llama.cpp server."),
    )
    runtime_binary_path = forms.CharField(
        label=_("Runtime binary"),
        required=False,
        initial="llama-server",
        help_text=_("Executable path or command name for the local llama.cpp server."),
    )
    probe_runtime = forms.BooleanField(
        label=_("Probe the runtime now"),
        required=False,
        initial=True,
        help_text=_("Checks /models and records the resolved runtime model before scheduling the task."),
    )

    def clean(self):
        """Validate model-backed runtime settings."""

        cleaned = super().clean()
        selected_model = (cleaned.get("selected_model") or "").strip()
        model_path = (cleaned.get("model_path") or "").strip()
        runtime_base_url = (cleaned.get("runtime_base_url") or "").strip()
        runtime_binary_path = (cleaned.get("runtime_binary_path") or "").strip()
        backend = cleaned.get("backend")

        cleaned["selected_model"] = selected_model
        cleaned["model_path"] = model_path
        cleaned["runtime_base_url"] = runtime_base_url
        cleaned["runtime_binary_path"] = runtime_binary_path

        if backend == LLMSummaryConfig.Backend.LLAMA_CPP_SERVER:
            if not selected_model:
                self.add_error("selected_model", _("Select a model before enabling the llama.cpp runtime."))
            if not runtime_base_url:
                self.add_error("runtime_base_url", _("Enter the local runtime base URL."))
            if not runtime_binary_path:
                self.add_error("runtime_binary_path", _("Enter the llama.cpp server binary name or path."))
        return cleaned


@admin.register(LLMSummaryConfig)
class LLMSummaryConfigAdmin(admin.ModelAdmin):
    """Admin integration for LCD summary runtime settings."""

    list_display = ("display", "slug", "backend", "is_active", "installed_at", "last_run_at")
    list_filter = ("backend", "is_active")
    search_fields = ("slug", "display")
    readonly_fields = (
        "model_command_audit",
        "installed_at",
        "last_run_at",
        "created_at",
        "updated_at",
    )
    change_list_template = "admin/summary/llmsummaryconfig/change_list.html"

    def get_urls(self):
        """Add the summary configuration wizard endpoint."""

        custom = [
            path(
                "wizard/",
                self.admin_site.admin_view(self.model_wizard_view),
                name="summary_llmsummaryconfig_wizard",
            ),
        ]
        return custom + super().get_urls()

    def model_wizard_view(self, request: HttpRequest) -> HttpResponse:
        """Render and process the operator wizard for summary setup."""

        if not self.has_change_permission(request):
            messages.error(
                request, _("You do not have permission to configure LLM summaries.")
            )
            return redirect("admin:index")

        config = get_summary_config()
        resolved_path = resolve_model_path(config)
        form = LLMSummaryWizardForm(
            request.POST or None,
            initial={
                "selected_model": config.selected_model,
                "model_path": config.model_path or str(resolved_path),
                "backend": config.backend,
                "runtime_base_url": resolve_runtime_base_url(config),
                "runtime_binary_path": resolve_runtime_binary_path(config),
            },
        )

        if request.method == "POST" and form.is_valid():
            config.backend = form.cleaned_data["backend"]
            config.selected_model = form.cleaned_data["selected_model"]
            config.model_path = form.cleaned_data.get("model_path", "").strip()
            config.runtime_base_url = form.cleaned_data.get("runtime_base_url", "").strip()
            config.runtime_binary_path = form.cleaned_data.get("runtime_binary_path", "").strip()
            config.runtime_model_id = ""
            config.runtime_is_ready = False
            config.last_runtime_error = ""
            config.save(
                update_fields=[
                    "backend",
                    "selected_model",
                    "model_path",
                    "runtime_base_url",
                    "runtime_binary_path",
                    "runtime_model_id",
                    "runtime_is_ready",
                    "last_runtime_error",
                    "updated_at",
                ]
            )

            if form.cleaned_data.get("probe_runtime"):
                runtime_state = probe_summary_runtime(config)
                if runtime_state.ready:
                    messages.success(request, runtime_state.detail)
                else:
                    messages.warning(request, runtime_state.detail)

            sync_summary_suite_feature(config)
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

    def _build_setup_checks(
        self,
        config: LLMSummaryConfig,
        *,
        resolved_path: Path,
    ) -> list[dict[str, str]]:
        """Return operator-facing setup checklist rows for the wizard."""

        from apps.nodes.models import Node
        from apps.summary.node_features import get_llm_summary_prereq_state

        checks: list[dict[str, str]] = []
        from apps.features.models import Feature

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
                "detail": _(
                    "Register this host as a local node before enabling summaries."
                ),
            })
            return checks

        checks.append({
            "label": _("Node feature assignment"),
            "status": _("Ready") if node.has_feature("llm-summary") else _("Missing"),
            "detail": _("Assign the LLM Summary node feature to this node."),
        })
        checks.append({
            "label": _("LCD node feature"),
            "status": _("Ready") if node.has_feature("lcd-screen") else _("Missing"),
            "detail": _("The LCD Screen node feature must stay assigned for LCD summary rotation."),
        })

        prereqs = get_llm_summary_prereq_state(
            base_dir=Path(settings.BASE_DIR),
            base_path=node.get_base_path(),
        )
        try:
            runtime_command = build_summary_runtime_launch_plan(config).audit_command
        except ValueError:
            runtime_command = config.model_command_audit or _(
                "Choose a model and a local runtime endpoint to generate the managed command."
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
            "label": _("Summary backend"),
            "status": config.get_backend_display(),
            "detail": _("The preferred backend is a local llama.cpp server that exposes an OpenAI-compatible API."),
        })
        checks.append({
            "label": _("Runtime binary"),
            "status": resolve_runtime_binary_path(config),
            "detail": _("Arthexis uses this binary name/path when it launches the managed llama.cpp service."),
        })
        selected_model = get_selected_summary_model(config)
        checks.append({
            "label": _("Selected model"),
            "status": selected_model.display if selected_model else _("Missing"),
            "detail": (
                selected_model.notes
                if selected_model is not None
                else _("Choose one built-in model before the summary task can be scheduled.")
            ),
        })
        checks.append({
            "label": _("Model directory"),
            "status": _("Ready") if resolved_path.exists() else _("Missing"),
            "detail": _(
                "Optional local artifact or cache path recorded for operator reference."
            ),
        })
        checks.append({
            "label": _("Runtime probe"),
            "status": _("Ready") if summary_runtime_is_ready(config) else _("Missing"),
            "detail": (
                config.runtime_model_id
                or config.last_runtime_error
                or _("Probe the runtime after selecting a model and base URL.")
            ),
        })
        checks.append({
            "label": _("Runtime service lock"),
            "status": _("Ready") if summary_runtime_service_lock_enabled(base_dir=Path(settings.BASE_DIR)) else _("Missing"),
            "detail": runtime_command,
        })
        checks.append({
            "label": _("Reviewed"),
            "status": timezone.now().strftime("%Y-%m-%d %H:%M"),
            "detail": "",
        })
        return checks
