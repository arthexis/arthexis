"""Admin registrations for Raspberry Pi Connect integration models."""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.rpiconnect.models import (
    ConnectAccount,
    ConnectCampaignEvent,
    ConnectDevice,
    ConnectImageRelease,
    ConnectIngestionEvent,
    ConnectUpdateCampaign,
    ConnectUpdateDeployment,
)
from apps.rpiconnect.services import CampaignService, CampaignServiceError


class ConnectAccountAdminForm(forms.ModelForm):
    class Meta:
        model = ConnectAccount
        fields = "__all__"
        widgets = {
            "refresh_token_reference": forms.PasswordInput(render_value=True),
            "token_reference": forms.PasswordInput(render_value=True),
        }


class ConnectCampaignWizardForm(forms.Form):
    """Collect campaign targeting and rollout controls for admin operators."""

    release = forms.ModelChoiceField(
        queryset=ConnectImageRelease.objects.order_by("-released_at", "name", "version"),
        help_text=_("Release to deploy. Checksums and compatibility tags are shown in the registry list."),
        label=_("Release to deploy"),
    )
    device_ids = forms.CharField(
        help_text=_("Comma-separated external device IDs (for explicit targeting)."),
        label=_("Target specific device IDs"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    labels = forms.CharField(
        help_text=_("Comma-separated metadata labels; devices with any listed label are included."),
        label=_("Target by metadata labels"),
        required=False,
    )
    cohorts = forms.CharField(
        help_text=_("Comma-separated cohort values from device metadata (for staged populations)."),
        label=_("Target by metadata cohorts"),
        required=False,
    )
    strategy = forms.ChoiceField(
        choices=ConnectUpdateCampaign.Strategy.choices,
        help_text=_("Rollout strategy controls whether all targets, canary sets, or batches queue first."),
        label=_("Rollout strategy"),
    )
    canary_percent = forms.IntegerField(
        help_text=_("Canary percentage of matched devices to queue first when strategy is Canary."),
        initial=10,
        label=_("Canary percentage"),
        max_value=100,
        min_value=1,
        required=False,
    )
    batch_size = forms.IntegerField(
        help_text=_("Number of devices queued per stage when strategy is Batched."),
        initial=25,
        label=_("Batch size"),
        min_value=1,
        required=False,
    )
    launch_timing = forms.ChoiceField(
        choices=(
            ("start_now", _("Start now")),
            ("draft", _("Save as draft")),
        ),
        help_text=_("Choose whether to launch immediately or leave the campaign in draft for manual timing."),
        initial="start_now",
        label=_("Campaign launch timing"),
    )
    timing_notes = forms.CharField(
        help_text=_("Optional operator timing context, such as maintenance window expectations."),
        label=_("Timing and scheduling notes"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    notes = forms.CharField(
        help_text=_("Operator notes shown on the campaign and event timeline."),
        label=_("Campaign notes"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    override_conflicts = forms.BooleanField(
        help_text=_("Allow launch even when devices are part of another active campaign."),
        label=_("Override campaign conflict protection"),
        required=False,
    )

    @staticmethod
    def _split_tokens(raw_value: str) -> list[str]:
        return [token.strip() for token in raw_value.replace("\n", ",").split(",") if token.strip()]

    def clean(self):
        cleaned_data = super().clean()
        device_ids = self._split_tokens(cleaned_data.get("device_ids", ""))
        labels = self._split_tokens(cleaned_data.get("labels", ""))
        cohorts = self._split_tokens(cleaned_data.get("cohorts", ""))

        if not any([device_ids, labels, cohorts]):
            raise ValidationError(_("Provide at least one targeting selector: device IDs, labels, or cohorts."))

        cleaned_data["target_set"] = {
            "cohorts": cohorts,
            "device_ids": device_ids,
            "labels": labels,
        }
        return cleaned_data


@admin.register(ConnectAccount)
class ConnectAccountAdmin(admin.ModelAdmin):
    form = ConnectAccountAdminForm
    list_display = ("name", "account_type", "organization_name", "owner_email")
    list_filter = ("account_type", "created_at")
    search_fields = ("name", "organization_name", "owner_name", "owner_email")


@admin.register(ConnectDevice)
class ConnectDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "device_id",
        "account",
        "hardware_model",
        "os_release",
        "connectivity_indicator",
        "free_space_indicator",
        "eligibility_indicator",
        "is_online",
        "last_seen",
        "enrollment_source",
    )
    list_filter = ("is_online", "enrollment_source", "hardware_model")
    search_fields = ("device_id", "hardware_model", "os_release", "account__name")

    @admin.display(description=_("Connectivity signal"))
    def connectivity_indicator(self, obj: ConnectDevice) -> str:
        connectivity = obj.metadata.get("connectivity") if isinstance(obj.metadata, dict) else None
        if isinstance(connectivity, str) and connectivity.strip():
            return connectivity.strip()
        return _("Unknown")

    @admin.display(description=_("Reported free space"))
    def free_space_indicator(self, obj: ConnectDevice) -> str:
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        free_space = metadata.get("free_space") or metadata.get("free_space_bytes")
        if free_space in {None, ""}:
            return _("Not reported")
        return str(free_space)

    @admin.display(description=_("Eligibility indicator"))
    def eligibility_indicator(self, obj: ConnectDevice) -> str:
        missing: list[str] = []
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        if not obj.hardware_model:
            missing.append(_("Pi model"))
        if not obj.os_release:
            missing.append(_("OS release"))
        if not metadata.get("connectivity"):
            missing.append(_("connectivity"))
        if not (metadata.get("free_space") or metadata.get("free_space_bytes")):
            missing.append(_("free space"))

        if missing:
            return _("Review required: %(missing)s") % {"missing": ", ".join(missing)}
        return _("Eligible: inventory signals complete")


@admin.register(ConnectImageRelease)
class ConnectImageReleaseAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "version",
        "compatibility_summary",
        "checksum",
        "released_at",
    )
    list_filter = ("released_at",)
    search_fields = ("name", "version", "checksum")

    @admin.display(description=_("Compatibility summary"))
    def compatibility_summary(self, obj: ConnectImageRelease) -> str:
        tags = obj.compatibility_tags if isinstance(obj.compatibility_tags, list) else []
        if not tags:
            return _("All tracked devices (no compatibility tags provided)")
        preview = ", ".join(str(tag) for tag in tags[:4])
        if len(tags) > 4:
            return _("Targets tagged devices: %(preview)s, +%(extra)s more") % {
                "extra": len(tags) - 4,
                "preview": preview,
            }
        return _("Targets tagged devices: %(preview)s") % {"preview": preview}


@admin.register(ConnectUpdateCampaign)
class ConnectUpdateCampaignAdmin(admin.ModelAdmin):
    change_form_template = "admin/rpiconnect/connectupdatecampaign/change_form.html"
    change_list_template = "admin/rpiconnect/connectupdatecampaign/change_list.html"
    list_display = (
        "id",
        "release",
        "strategy",
        "status",
        "progress_summary",
        "created_by",
        "started_at",
        "completed_at",
    )
    list_filter = ("strategy", "status", "started_at", "completed_at")
    search_fields = ("id", "release__name")

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).annotate(
            failed_count=Count(
                "deployments",
                filter=Q(deployments__status=ConnectUpdateDeployment.Status.FAILED),
            ),
            succeeded_count=Count(
                "deployments",
                filter=Q(deployments__status=ConnectUpdateDeployment.Status.SUCCEEDED),
            ),
            total_count=Count("deployments"),
        )

    def get_urls(self):
        custom_urls = [
            path("wizard/", self.admin_site.admin_view(self.campaign_wizard_view), name="rpiconnect_campaign_wizard"),
            path(
                "<int:campaign_id>/progress/",
                self.admin_site.admin_view(self.campaign_progress_view),
                name="rpiconnect_campaign_progress",
            ),
            path(
                "<int:campaign_id>/rollback/",
                self.admin_site.admin_view(self.rollback_campaign_view),
                name="rpiconnect_campaign_rollback",
            ),
        ]
        return custom_urls + super().get_urls()

    def changelist_view(self, request: HttpRequest, extra_context=None):
        extra_context = extra_context or {}
        extra_context["campaign_wizard_url"] = reverse("admin:rpiconnect_campaign_wizard")
        return super().changelist_view(request, extra_context=extra_context)

    def change_view(self, request: HttpRequest, object_id: str, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(
            {
                "campaign_progress_url": reverse("admin:rpiconnect_campaign_progress", args=[object_id]),
                "campaign_rollback_url": reverse("admin:rpiconnect_campaign_rollback", args=[object_id]),
            }
        )
        return super().change_view(request, object_id, form_url=form_url, extra_context=extra_context)

    @admin.display(description=_("Progress"))
    def progress_summary(self, obj: ConnectUpdateCampaign) -> str:
        total = getattr(obj, "total_count", None)
        if total is None:
            totals = obj.deployments.values("status").annotate(count=Count("id"))
            status_counts = {entry["status"]: entry["count"] for entry in totals}
            total = sum(status_counts.values())
            succeeded = status_counts.get(ConnectUpdateDeployment.Status.SUCCEEDED, 0)
            failed = status_counts.get(ConnectUpdateDeployment.Status.FAILED, 0)
        else:
            succeeded = getattr(obj, "succeeded_count", 0)
            failed = getattr(obj, "failed_count", 0)

        if total == 0:
            return _("No deployments queued")
        return _("%(succeeded)s/%(total)s succeeded, %(failed)s failed") % {
            "failed": failed,
            "succeeded": succeeded,
            "total": total,
        }

    def campaign_wizard_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_add_permission(request):
            messages.error(request, _("You do not have permission to create campaigns."))
            return HttpResponseRedirect(reverse("admin:rpiconnect_connectupdatecampaign_changelist"))

        form = ConnectCampaignWizardForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            cleaned = form.cleaned_data
            release = cleaned["release"]
            strategy = cleaned["strategy"]
            target_set = cleaned["target_set"]
            notes_sections = [cleaned.get("notes", "").strip()]
            if cleaned.get("timing_notes"):
                notes_sections.append(f"Timing notes: {cleaned['timing_notes'].strip()}")
            notes = "\n\n".join(section for section in notes_sections if section)

            try:
                campaign = CampaignService().create_campaign(
                    release=release,
                    target_set=target_set,
                    strategy=strategy,
                    created_by=request.user,
                    notes=notes,
                    override_conflicts=cleaned.get("override_conflicts", False),
                    batch_size=cleaned.get("batch_size") or 0,
                    canary_percent=cleaned.get("canary_percent") or 10,
                )
                if cleaned.get("launch_timing") == "start_now":
                    CampaignService().start_campaign(campaign, created_by=request.user)
            except CampaignServiceError as exc:
                form.add_error(None, exc)
            else:
                creation_event = campaign.events.filter(event_type=CampaignService.EVENT_CAMPAIGN_CREATED).first()
                target_count = (creation_event.payload or {}).get("target_count", 0) if creation_event else 0
                messages.success(
                    request,
                    _("Campaign %(campaign)s created with %(count)s targeted devices.")
                    % {"campaign": campaign.pk, "count": target_count},
                )
                return HttpResponseRedirect(
                    reverse("admin:rpiconnect_connectupdatecampaign_change", args=[campaign.pk])
                )

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "opts": self.model._meta,
            "title": _("Campaign wizard"),
        }
        return TemplateResponse(request, "admin/rpiconnect/connectupdatecampaign/campaign_wizard.html", context)

    def campaign_progress_view(self, request: HttpRequest, campaign_id: int) -> HttpResponse:
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        campaign = (
            ConnectUpdateCampaign.objects.select_related("release", "created_by")
            .prefetch_related("deployments__device")
            .get(pk=campaign_id)
        )
        if not self.has_view_or_change_permission(request, campaign):
            raise PermissionDenied
        status_counts = {
            entry["status"]: entry["count"]
            for entry in campaign.deployments.values("status").annotate(count=Count("id")).order_by("status")
        }
        failed_deployments = campaign.deployments.filter(
            Q(status=ConnectUpdateDeployment.Status.FAILED)
            | Q(status=ConnectUpdateDeployment.Status.ROLLED_BACK)
        ).select_related("device")
        context = {
            **self.admin_site.each_context(request),
            "campaign": campaign,
            "failed_deployments": failed_deployments,
            "opts": self.model._meta,
            "status_counts": status_counts,
            "title": _("Live campaign progress"),
        }
        return TemplateResponse(request, "admin/rpiconnect/connectupdatecampaign/progress.html", context)

    def rollback_campaign_view(self, request: HttpRequest, campaign_id: int) -> HttpResponse:
        campaign = ConnectUpdateCampaign.objects.select_related("release").get(pk=campaign_id)
        if not self.has_change_permission(request, campaign) or not self.has_add_permission(request):
            raise PermissionDenied
        if request.method != "POST":
            messages.warning(request, _("Use the rollback button from the campaign form to confirm this action."))
            return HttpResponseRedirect(reverse("admin:rpiconnect_connectupdatecampaign_change", args=[campaign.pk]))

        previous_release = self._find_previous_known_good_release(campaign)
        if previous_release is None:
            messages.error(request, _("No previous known-good release could be identified for rollback."))
            return HttpResponseRedirect(reverse("admin:rpiconnect_connectupdatecampaign_change", args=[campaign.pk]))

        successful_device_ids = list(
            campaign.deployments.filter(status=ConnectUpdateDeployment.Status.SUCCEEDED).values_list(
                "device__device_id", flat=True
            )
        )
        if not successful_device_ids:
            messages.error(request, _("Rollback requires at least one succeeded deployment in the source campaign."))
            return HttpResponseRedirect(reverse("admin:rpiconnect_connectupdatecampaign_change", args=[campaign.pk]))

        rollback_campaign = CampaignService().create_campaign(
            release=previous_release,
            target_set={"cohorts": [], "device_ids": successful_device_ids, "labels": []},
            strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
            created_by=request.user,
            notes=_("Rollback from campaign %(campaign)s to previous known-good release.") % {"campaign": campaign.pk},
            override_conflicts=True,
        )
        CampaignService().start_campaign(rollback_campaign, created_by=request.user)

        messages.success(
            request,
            _("Rollback campaign %(rollback)s created using release %(release)s.")
            % {"release": previous_release, "rollback": rollback_campaign.pk},
        )
        return HttpResponseRedirect(
            reverse("admin:rpiconnect_connectupdatecampaign_change", args=[rollback_campaign.pk])
        )

    def _find_previous_known_good_release(self, campaign: ConnectUpdateCampaign) -> ConnectImageRelease | None:
        successful_device_ids = campaign.deployments.filter(
            status=ConnectUpdateDeployment.Status.SUCCEEDED
        ).values_list("device_id", flat=True)
        if not successful_device_ids:
            return None

        successful_campaign_ids = ConnectUpdateCampaign.objects.filter(
            deployments__device_id__in=successful_device_ids,
            deployments__status=ConnectUpdateDeployment.Status.SUCCEEDED,
            status=ConnectUpdateCampaign.Status.COMPLETED,
        ).exclude(pk=campaign.pk)

        previous_release_qs = (
            ConnectImageRelease.objects.filter(campaigns__in=successful_campaign_ids)
            .exclude(pk=campaign.release_id)
            .order_by("-released_at", "-created_at")
            .distinct()
        )
        return previous_release_qs.first()


@admin.register(ConnectUpdateDeployment)
class ConnectUpdateDeploymentAdmin(admin.ModelAdmin):
    list_display = (
        "campaign",
        "device",
        "status",
        "failure_classification",
        "retry_attempts",
        "next_retry_at",
        "started_at",
        "completed_at",
    )
    list_filter = ("status", "failure_classification", "started_at", "completed_at")
    search_fields = ("device__device_id", "campaign__id", "campaign__release__name")


@admin.register(ConnectCampaignEvent)
class ConnectCampaignEventAdmin(admin.ModelAdmin):
    list_display = (
        "campaign",
        "deployment",
        "event_type",
        "from_status",
        "to_status",
        "created_by",
        "created_at",
    )
    list_filter = ("event_type", "from_status", "to_status")
    search_fields = ("campaign__id", "deployment__id", "event_type")


@admin.register(ConnectIngestionEvent)
class ConnectIngestionEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_id",
        "event_type",
        "external_device_id",
        "status",
        "failure_classification",
        "retry_attempt",
        "cooldown_until",
        "created_at",
    )
    list_filter = ("event_type", "failure_classification", "status")
    readonly_fields = ("payload_snippet", "normalized_payload")
    search_fields = ("event_id", "external_device_id", "deployment__id", "campaign__id")
