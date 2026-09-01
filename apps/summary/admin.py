from __future__ import annotations

from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.features.models import Feature
from apps.nodes.models import NodeFeature

from .constants import LLM_SUMMARY_NODE_FEATURE_SLUG, LLM_SUMMARY_SUITE_FEATURE_SLUG
from .models import LLMSummaryConfig
from .services import get_summary_config, resolve_summary_output_file_path


class LLMSummaryWizardForm(forms.Form):
    """Collect deterministic LCD summary settings for operators."""

    is_active = forms.BooleanField(
        label=_("Enable deterministic summaries"),
        required=False,
        initial=True,
    )
    output_target = forms.ChoiceField(
        label=_("Output target"),
        choices=LLMSummaryConfig.OutputTarget.choices,
        initial=LLMSummaryConfig.OutputTarget.LCD,
    )
    output_file_path = forms.CharField(
        label=_("Output file path"),
        required=False,
        help_text=_(
            "Relative path under logs/summary. Leave empty for logs/summary/latest.txt."
        ),
    )
    output_file_format = forms.ChoiceField(
        label=_("Output file format"),
        choices=LLMSummaryConfig.OutputFileFormat.choices,
        initial=LLMSummaryConfig.OutputFileFormat.TEXT,
    )


@admin.register(LLMSummaryConfig)
class LLMSummaryConfigAdmin(admin.ModelAdmin):
    """Admin integration for deterministic LCD summary runtime settings."""

    list_display = (
        "display",
        "slug",
        "output_target",
        "output_file_format",
        "is_active",
        "last_run_at",
    )
    list_filter = ("output_target", "output_file_format", "is_active")
    search_fields = ("slug", "display")
    readonly_fields = (
        "last_run_at",
        "last_output_file_path",
        "created_at",
        "updated_at",
    )
    change_list_template = "admin/summary/llmsummaryconfig/change_list.html"

    def _sync_summary_suite_feature(self, config: LLMSummaryConfig) -> Feature:
        """Ensure deterministic summary automation remains linked to its feature."""

        summary_node_feature = NodeFeature.objects.filter(
            slug=LLM_SUMMARY_NODE_FEATURE_SLUG
        ).first()
        suite_feature, _created = Feature.objects.get_or_create(
            slug=LLM_SUMMARY_SUITE_FEATURE_SLUG,
            defaults={
                "display": "Deterministic Summary Suite",
                "source": Feature.Source.CUSTOM,
                "is_enabled": True,
                "node_feature": summary_node_feature,
            },
        )
        updated_fields: set[str] = set()
        if suite_feature.node_feature_id != (
            summary_node_feature.pk if summary_node_feature else None
        ):
            suite_feature.node_feature = summary_node_feature
            updated_fields.add("node_feature")
        if suite_feature.display != "Deterministic Summary Suite":
            suite_feature.display = "Deterministic Summary Suite"
            updated_fields.add("display")

        stale_parameters = {
            "backend",
            "model_path",
            "ollama_base_url",
            "ollama_context_tokens",
            "ollama_fallback_to_deterministic",
            "ollama_keep_alive",
            "ollama_lock_timeout_seconds",
            "ollama_max_output_tokens",
            "ollama_max_prompt_bytes",
            "ollama_model",
            "ollama_request_timeout_seconds",
        }
        metadata = (
            suite_feature.metadata if isinstance(suite_feature.metadata, dict) else {}
        )
        parameters = metadata.get("parameters", {})
        if isinstance(parameters, dict):
            removed = False
            for key in stale_parameters:
                if key in parameters:
                    parameters.pop(key, None)
                    removed = True
            if removed:
                metadata["parameters"] = parameters
                suite_feature.metadata = metadata
                updated_fields.add("metadata")

        if updated_fields:
            updated_fields.add("updated_at")
            suite_feature.save(update_fields=sorted(updated_fields))
        return suite_feature

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

    def save_model(self, request, obj, form, change) -> None:
        """Persist config changes and prune obsolete model metadata."""

        super().save_model(request, obj, form, change)
        self._sync_summary_suite_feature(obj)

    def model_wizard_view(self, request: HttpRequest) -> HttpResponse:
        """Render and process the deterministic summary setup wizard."""

        if not self.has_change_permission(request):
            messages.error(
                request,
                _("You do not have permission to configure deterministic summaries."),
            )
            return redirect("admin:index")

        config = get_summary_config()
        form = LLMSummaryWizardForm(
            request.POST or None,
            initial={
                "is_active": config.is_active,
                "output_target": config.output_target,
                "output_file_path": config.output_file_path,
                "output_file_format": config.output_file_format,
            },
        )

        if request.method == "POST" and form.is_valid():
            config.is_active = bool(form.cleaned_data.get("is_active"))
            config.output_target = form.cleaned_data["output_target"]
            config.output_file_path = (
                form.cleaned_data.get("output_file_path") or ""
            ).strip()
            config.output_file_format = form.cleaned_data["output_file_format"]
            try:
                config.full_clean()
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                config.save(
                    update_fields=[
                        "is_active",
                        "output_target",
                        "output_file_path",
                        "output_file_format",
                        "updated_at",
                    ]
                )
                self._sync_summary_suite_feature(config)
                messages.success(request, _("Deterministic summary settings updated."))
                return redirect(
                    reverse(
                        "admin:summary_llmsummaryconfig_change",
                        args=[config.pk],
                    )
                )

        checks = self._build_setup_checks(config)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "form": form,
            "config": config,
            "title": _("Deterministic Summary Wizard"),
            "breadcrumbs_title": _("Deterministic Summary Wizard"),
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
    ) -> list[dict[str, str]]:
        """Return operator-facing setup checklist rows for the wizard."""

        from apps.nodes.models import Node
        from apps.summary.node_features import get_llm_summary_prereq_state

        checks: list[dict[str, str]] = []
        suite_feature = Feature.objects.filter(
            slug=LLM_SUMMARY_SUITE_FEATURE_SLUG
        ).first()
        suite_enabled = bool(suite_feature and suite_feature.is_enabled)
        checks.append(
            {
                "label": _("Suite feature enabled"),
                "status": _("Ready") if suite_enabled else _("Missing"),
                "detail": _(
                    "Enable the deterministic summary suite feature to allow automation."
                ),
            }
        )

        node = Node.get_local()
        if node is None:
            checks.append(
                {
                    "label": _("Local node registration"),
                    "status": _("Missing"),
                    "detail": _(
                        "Register this host as a local node before enabling summaries."
                    ),
                }
            )
            return checks

        checks.append(
            {
                "label": _("Node feature assignment"),
                "status": (
                    _("Ready") if node.has_feature("llm-summary") else _("Missing")
                ),
                "detail": _("Assign the summary node feature to this node."),
            }
        )

        prereqs = get_llm_summary_prereq_state(
            base_dir=Path(settings.BASE_DIR),
            base_path=node.get_base_path(),
        )
        checks.append(
            {
                "label": _("Celery lock"),
                "status": _("Ready") if prereqs["celery_enabled"] else _("Missing"),
                "detail": _("Enable the Celery Queue node feature and lock file."),
            }
        )
        checks.append(
            {
                "label": _("Summary configuration active"),
                "status": _("Ready") if config.is_active else _("Missing"),
                "detail": _(
                    "Activate the summary config to permit runtime generation."
                ),
            }
        )
        checks.append(
            {
                "label": _("Summary mode"),
                "status": _("Deterministic"),
                "detail": _(
                    "Summaries run in process with no local model, provider, or shell command."
                ),
            }
        )
        try:
            output_path = resolve_summary_output_file_path(
                config,
                base_dir=Path(settings.BASE_DIR),
            )
        except ValueError as exc:
            output_path = _("Invalid: %(error)s") % {"error": exc}
        checks.append(
            {
                "label": _("Output path"),
                "status": str(output_path),
                "detail": _("Used only when output target is File."),
            }
        )
        checks.append(
            {
                "label": _("Reviewed"),
                "status": timezone.now().strftime("%Y-%m-%d %H:%M"),
                "detail": "",
            }
        )
        return checks
