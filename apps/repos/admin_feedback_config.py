"""Shared admin tooling for configuring feedback-to-GitHub issue requirements."""

from __future__ import annotations

from dataclasses import dataclass

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.utils import OperationalError, ProgrammingError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.translation import gettext_lazy as _

from apps.celery.utils import is_celery_enabled
from apps.features.models import Feature
from apps.release import DEFAULT_PACKAGE
from apps.release.models import Package
from apps.repos.github import parse_repository_url
from apps.repos.issue_reporting import GITHUB_ISSUE_REPORTING_FEATURE_SLUG
from apps.repos.services.github import get_github_issue_token


@dataclass(frozen=True)
class FeedbackValidationItem:
    """Represents one validation row in the configure admin view."""

    label: str
    ok: bool
    detail: str
    guidance: str = ""
    editable: bool = False


class FeedbackIssueConfigurationForm(forms.Form):
    """Allows staff to update fields that can be changed from the configure screen."""

    feedback_ingestion_enabled = forms.BooleanField(
        required=False,
        label=_("Enable feedback ingestion feature"),
        help_text=_("Controls whether feedback forms can be submitted."),
    )
    github_issue_reporting_enabled = forms.BooleanField(
        required=False,
        label=_("Enable automatic GitHub exception reporting"),
        help_text=_(
            "Controls whether request exceptions enqueue automatic GitHub issue reporting."
        ),
    )
    active_repository_url = forms.URLField(
        required=False,
        assume_scheme="https",
        label=_("Active package repository URL"),
        help_text=_("Repository used for creating feedback GitHub issues."),
    )


class FeedbackIssueConfigurationAdminMixin:
    """Adds a non-queryset Configure action and view for feedback issue prerequisites."""

    change_actions = ("configure_action",)

    def _configure_route_names(self) -> list[str]:
        """Return route names for the configure screen, including legacy aliases."""

        opts = self.model._meta
        names = [f"{opts.app_label}_{opts.model_name}_configure"]

        model_module = getattr(self.model, "__module__", "")
        if ".sites." in model_module and opts.app_label != "sites":
            names.append(f"sites_{opts.model_name}_configure")
        return names

    def get_urls(self):
        """Register a per-object configure route for this admin."""

        urls = super().get_urls()
        custom_urls = []
        for route_name in self._configure_route_names():
            custom_urls.append(
                path(
                    "<path:object_id>/configure/",
                    self.admin_site.admin_view(self.configure_view),
                    name=route_name,
                )
            )
        return custom_urls + urls

    def _configure_url(self, obj) -> str:
        """Return the admin URL for the configure screen."""

        for route_name in self._configure_route_names():
            try:
                return reverse(
                    f"admin:{route_name}",
                    kwargs={"object_id": obj.pk},
                )
            except NoReverseMatch:
                continue
        opts = self.model._meta
        return reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change",
            args=[obj.pk],
        )

    def configure_action(self, request, obj):
        """Redirect the change form object-tool button to the configure screen."""

        return HttpResponseRedirect(self._configure_url(obj))

    configure_action.label = _("Configure")
    configure_action.short_description = _("Configure")
    configure_action.requires_queryset = False

    def _active_package(self) -> Package | None:
        """Return the active package record used for repository selection."""

        return Package.objects.filter(is_active=True).first()

    def _get_feedback_feature(self) -> Feature | None:
        """Return the feedback ingestion feature when the suite-features table exists."""

        try:
            return Feature.objects.filter(slug="feedback-ingestion").first()
        except (OperationalError, ProgrammingError):
            return None

    def _get_or_create_feedback_feature(self) -> tuple[Feature | None, bool]:
        """Return the feedback ingestion feature when persistence is available."""

        try:
            return Feature.objects.get_or_create(
                slug="feedback-ingestion",
                defaults={"display": "Feedback Ingestion", "is_enabled": True},
            )
        except (OperationalError, ProgrammingError):
            return None, False

    def _get_github_issue_reporting_feature(self) -> Feature | None:
        """Return the automatic GitHub issue reporting feature when available."""

        try:
            return Feature.objects.filter(
                slug=GITHUB_ISSUE_REPORTING_FEATURE_SLUG
            ).first()
        except (OperationalError, ProgrammingError):
            return None

    def _github_issue_reporting_default_enabled(self) -> bool:
        """Return the legacy default used before the suite feature exists."""

        return bool(getattr(settings, "GITHUB_ISSUE_REPORTING_ENABLED", True))

    def _get_or_create_github_issue_reporting_feature(
        self,
        *,
        enabled: bool | None = None,
    ) -> tuple[Feature | None, bool]:
        """Return the automatic GitHub issue reporting feature when persistence is available."""

        if enabled is None:
            enabled = self._github_issue_reporting_default_enabled()

        try:
            return Feature.objects.get_or_create(
                slug=GITHUB_ISSUE_REPORTING_FEATURE_SLUG,
                defaults={
                    "display": "GitHub Issue Reporting",
                    "is_enabled": enabled,
                },
            )
        except (OperationalError, ProgrammingError):
            return None, False

    def _build_validation_items(self) -> list[FeedbackValidationItem]:
        """Return validation rows that describe readiness for feedback issue creation."""

        feature = self._get_feedback_feature()
        github_issue_reporting_feature = self._get_github_issue_reporting_feature()
        active_package = self._active_package()
        try:
            token_configured = bool(get_github_issue_token())
        except RuntimeError:
            token_configured = False
        repository_url = (
            active_package.repository_url.strip()
            if active_package and active_package.repository_url
            else DEFAULT_PACKAGE.repository_url
        )

        try:
            owner, name = parse_repository_url(repository_url)
        except ValueError:
            repository_ok = False
            repository_detail = _("Invalid repository URL: %(url)s") % {
                "url": repository_url,
            }
        else:
            repository_ok = True
            repository_detail = _("Resolved repository: %(owner)s/%(name)s") % {
                "owner": owner,
                "name": name,
            }

        return [
            FeedbackValidationItem(
                label=str(_("Feedback ingestion feature")),
                ok=bool(feature and feature.is_enabled),
                detail=str(
                    _("Enabled")
                    if feature and feature.is_enabled
                    else _("Disabled or missing")
                ),
                guidance=str(_("Enable this feature so forms can submit feedback.")),
                editable=True,
            ),
            FeedbackValidationItem(
                label=str(_("Automatic GitHub exception reporting")),
                ok=bool(
                    github_issue_reporting_feature
                    and github_issue_reporting_feature.is_enabled
                ),
                detail=str(
                    _("Enabled")
                    if github_issue_reporting_feature
                    and github_issue_reporting_feature.is_enabled
                    else _("Disabled or missing")
                ),
                guidance=str(
                    _(
                        "Enable this suite feature so request exceptions can enqueue GitHub issue reports."
                    )
                ),
                editable=True,
            ),
            FeedbackValidationItem(
                label=str(_("Active package")),
                ok=active_package is not None,
                detail=str(
                    _("%(name)s") % {"name": active_package.name}
                    if active_package
                    else _("No active package configured")
                ),
                guidance=str(_("Set one package as active in Release > Packages.")),
                editable=False,
            ),
            FeedbackValidationItem(
                label=str(_("Repository URL")),
                ok=repository_ok,
                detail=repository_detail,
                guidance=str(
                    _("Set a valid GitHub repository URL for the active package.")
                ),
                editable=active_package is not None,
            ),
            FeedbackValidationItem(
                label=str(_("GitHub token")),
                ok=token_configured,
                detail=str(_("Configured") if token_configured else _("Missing")),
                guidance=str(
                    _(
                        "Set a package release token or GITHUB_TOKEN in the runtime environment."
                    )
                ),
                editable=False,
            ),
            FeedbackValidationItem(
                label=str(_("Celery queue availability")),
                ok=is_celery_enabled(),
                detail=str(_("Enabled") if is_celery_enabled() else _("Disabled")),
                guidance=str(
                    _(
                        "Start Celery and ensure .locks/celery.lck exists; auto issue creation is queued."
                    )
                ),
                editable=False,
            ),
        ]

    def _initial_form_values(self) -> dict[str, object]:
        """Return initial values for editable configuration fields."""

        feature = self._get_feedback_feature()
        github_issue_reporting_feature = self._get_github_issue_reporting_feature()
        active_package = self._active_package()
        github_issue_reporting_enabled = (
            github_issue_reporting_feature.is_enabled
            if github_issue_reporting_feature is not None
            else self._github_issue_reporting_default_enabled()
        )
        return {
            "feedback_ingestion_enabled": bool(feature and feature.is_enabled),
            "github_issue_reporting_enabled": bool(github_issue_reporting_enabled),
            "active_repository_url": (
                active_package.repository_url if active_package else ""
            ),
        }

    def _save_configuration_form(
        self,
        *,
        request: HttpRequest,
        form: FeedbackIssueConfigurationForm,
    ) -> None:
        """Persist editable settings from the configure screen."""

        feature, _created = self._get_or_create_feedback_feature()
        enabled = bool(form.cleaned_data["feedback_ingestion_enabled"])
        if feature is None:
            self.message_user(
                request,
                _(
                    "Suite Features are unavailable because migrations have not finished yet."
                ),
                level=messages.WARNING,
            )
        elif feature.is_enabled != enabled:
            feature.is_enabled = enabled
            feature.save(update_fields=["is_enabled"])

        github_issue_reporting_enabled = bool(
            form.cleaned_data["github_issue_reporting_enabled"]
        )
        github_issue_reporting_feature, _created = (
            self._get_or_create_github_issue_reporting_feature(
                enabled=github_issue_reporting_enabled
            )
        )
        if github_issue_reporting_feature is None:
            self.message_user(
                request,
                _(
                    "Suite Features are unavailable because migrations have not finished yet."
                ),
                level=messages.WARNING,
            )
        elif (
            github_issue_reporting_feature.is_enabled
            != github_issue_reporting_enabled
        ):
            github_issue_reporting_feature.is_enabled = github_issue_reporting_enabled
            github_issue_reporting_feature.save(update_fields=["is_enabled"])

        active_package = self._active_package()
        repository_url = (form.cleaned_data.get("active_repository_url") or "").strip()
        if (
            active_package
            and repository_url
            and repository_url != active_package.repository_url
        ):
            active_package.repository_url = repository_url
            active_package.save(update_fields=["repository_url"])
        elif not active_package and repository_url:
            self.message_user(
                request,
                _(
                    "No active package exists. Create one in Release > Packages to persist the repository URL."
                ),
                level=messages.WARNING,
            )

    def configure_view(
        self, request: HttpRequest, object_id: str, *args, **kwargs
    ) -> HttpResponse:
        """Render and process feedback issue configuration for the current model admin."""

        obj = self.get_object(request, object_id)
        if obj is None:
            raise Http404(_("Object not found."))
        if not self.has_change_permission(request, obj):
            raise PermissionDenied

        if request.method == "POST":
            form = FeedbackIssueConfigurationForm(request.POST)
            if form.is_valid():
                self._save_configuration_form(request=request, form=form)
                self.message_user(
                    request,
                    _("Configuration updated. Validation has been re-run."),
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(self._configure_url(obj))
        else:
            form = FeedbackIssueConfigurationForm(initial=self._initial_form_values())

        opts = self.model._meta
        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "original": obj,
            "title": _("Configure feedback issue automation"),
            "form": form,
            "validation_items": self._build_validation_items(),
            "change_url": reverse(
                f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk]
            ),
        }
        return TemplateResponse(
            request,
            "admin/includes/feedback_issue_configure.html",
            context,
        )
