from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

import logging

from apps.locals.user_data import EntityModelAdmin
from apps.discovery.services import record_discovery_item, start_discovery

from ..models import Node, NodeFeature, NodeFeatureAssignment
from apps.content.utils import capture_screenshot, save_screenshot
from .actions import (
    check_features_for_eligibility,
    discover_node_features,
    enable_selected_features,
)
from .forms import NodeFeatureAdminForm
from .reports_admin import CeleryReportAdminMixin


@admin.register(NodeFeature)
class NodeFeatureAdmin(CeleryReportAdminMixin, EntityModelAdmin):
    CONTROL_MODE_MANUAL = "Manual"
    CONTROL_MODE_AUTO = "Auto"

    form = NodeFeatureAdminForm
    list_display = (
        "display",
        "slug",
        "default_roles",
        "control_mode",
        "is_enabled_display",
        "available_actions",
    )
    actions = [
        discover_node_features,
        check_features_for_eligibility,
        enable_selected_features,
    ]
    readonly_fields = ("is_enabled", "linked_features")
    search_fields = ("display", "slug")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("roles", "suite_features")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.slug == "llm-summary":
            self._report_prereq_checks(request, obj)

    @admin.display(description="Default Roles")
    def default_roles(self, obj):
        roles = [role.name for role in obj.roles.all()]
        return ", ".join(roles) if roles else "—"

    @admin.display(description="Control")
    def control_mode(self, obj):
        return (
            self.CONTROL_MODE_MANUAL
            if obj.slug in Node.MANUAL_FEATURE_SLUGS
            else self.CONTROL_MODE_AUTO
        )

    @admin.display(description="Is Enabled", boolean=True, ordering="is_enabled")
    def is_enabled_display(self, obj):
        return obj.is_enabled

    @admin.display(description="Actions")
    def available_actions(self, obj):
        if not obj.is_enabled:
            return "—"
        actions = obj.get_default_actions()
        if not actions:
            return "—"

        links = []
        for action in actions:
            try:
                url = reverse(action.url_name)
            except NoReverseMatch:
                links.append(action.label)
            else:
                links.append(format_html('<a href="{}">{}</a>', url, action.label))

        if not links:
            return "—"
        return format_html_join(" | ", "{}", ((link,) for link in links))

    @admin.display(description="Linked Features")
    def linked_features(self, obj):
        features = obj.suite_features.all()
        if not features:
            return "—"
        items = []
        for feature in features.order_by("display", "slug"):
            status = _("Enabled") if feature.is_enabled else _("Disabled")
            items.append(
                format_html(
                    "<li>{} <span class='help'>({})</span></li>",
                    feature.display,
                    status,
                )
            )
        return format_html("<ul>{}</ul>", format_html_join("", "{}", ((item,) for item in items)))

    def _manual_enablement_data(self, feature, node):
        if node is None:
            return {"status": "unavailable", "label": "Unavailable"}
        if feature.slug in Node.MANUAL_FEATURE_SLUGS:
            return {"status": "manual", "label": "Manual"}
        return {"status": "auto", "label": "Auto"}

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "discover/",
                self.admin_site.admin_view(self.discover_features),
                name="nodes_nodefeature_discover",
            ),
            path(
                "discover/progress/",
                self.admin_site.admin_view(self.discover_features_progress),
                name="nodes_nodefeature_discover_progress",
            ),
            path(
                "take-screenshot/",
                self.admin_site.admin_view(self.take_screenshot),
                name="nodes_nodefeature_take_screenshot",
            ),
        ]
        return custom + urls

    def _ensure_feature_enabled(self, request, slug: str, action_label: str):
        try:
            feature = NodeFeature.objects.get(slug=slug)
        except NodeFeature.DoesNotExist:
            self.message_user(
                request,
                f"{action_label} is unavailable because the feature is not configured.",
                level=messages.ERROR,
            )
            return None
        if not feature.is_enabled:
            self.message_user(
                request,
                f"{feature.display} feature is not enabled on this node.",
                level=messages.WARNING,
            )
            return None
        return feature

    def take_screenshot(self, request):
        feature = self._ensure_feature_enabled(
            request, "screenshot-poll", "Take Screenshot"
        )
        if not feature:
            return redirect("..")
        url = request.build_absolute_uri("/")
        try:
            path = capture_screenshot(url)
        except Exception as exc:  # pragma: no cover - depends on selenium setup
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")
        node = Node.get_local()
        sample = save_screenshot(path, node=node, method="DEFAULT_ACTION")
        if not sample:
            self.message_user(
                request, "Duplicate screenshot; not saved", level=messages.INFO
            )
            return redirect("..")
        self.message_user(
            request, f"Screenshot saved to {sample.path}", level=messages.SUCCESS
        )
        try:
            change_url = reverse(
                "admin:content_contentsample_change", args=[sample.pk]
            )
        except NoReverseMatch:  # pragma: no cover - admin URL always registered
            self.message_user(
                request,
                "Screenshot saved but the admin page could not be resolved.",
                level=messages.WARNING,
            )
            return redirect("..")
        return redirect(change_url)

    def discover_features(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        features = list(self.get_queryset(request))
        discovery = start_discovery(
            _("Discover"),
            request,
            model=self.model,
            metadata={"action": "node_feature_discover"},
        )
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Discover node features"),
            "features": features,
            "feature_ids": [str(feature.pk) for feature in features],
            "progress_url": reverse("admin:nodes_nodefeature_discover_progress"),
            "discovery_id": discovery.pk if discovery else "",
        }
        return TemplateResponse(
            request,
            "admin/nodes/nodefeature/discover.html",
            context,
        )

    def discover_features_progress(self, request):
        if request.method != "POST":
            return JsonResponse({"detail": "POST required"}, status=405)
        if not self.has_change_permission(request):
            raise PermissionDenied
        try:
            feature_id = int(request.POST.get("feature_id", ""))
        except (TypeError, ValueError):
            return JsonResponse({"detail": "Invalid feature id"}, status=400)
        discovery_id = request.POST.get("discovery_id") or ""
        feature = self.get_queryset(request).filter(pk=feature_id).first()
        if not feature:
            return JsonResponse({"detail": "Feature not found"}, status=404)

        node = Node.get_local()
        manual_enablement = self._manual_enablement_data(feature, node)

        status = "skipped"
        message = ""
        eligible = False
        level = messages.INFO
        try:
            from ..feature_checks import feature_checks

            result = feature_checks.run(feature, node=node)
        except Exception as exc:  # pragma: no cover - defensive
            logging.exception("Error while running feature check for %s", feature.display)
            status = "error"
            message = (
                f"An error occurred while checking eligibility for {feature.display}."
            )
            level = messages.ERROR
        else:
            if result is None:
                status = "skipped"
                message = f"No check is configured for {feature.display}."
                level = messages.WARNING
            else:
                eligible = bool(result.success)
                message = (
                    result.message
                    or f"{feature.display} check {'passed' if result.success else 'failed'}."
                )
                level = result.level
                status_map = {
                    messages.SUCCESS: "success",
                    messages.WARNING: "warning",
                    messages.ERROR: "error",
                }
                status = status_map.get(level, "info")

        enablement = {"status": "skipped", "message": "Not enabled."}
        assignment_created = False
        if eligible and node:
            assignment, created = NodeFeatureAssignment.objects.update_or_create(
                node=node, feature=feature
            )
            assignment_created = created
            if created:
                enablement = {
                    "status": "enabled",
                    "message": f"{feature.display} enabled.",
                }
            else:
                enablement = {
                    "status": "already_enabled",
                    "message": f"{feature.display} already enabled.",
                }
        elif eligible and not node:
            enablement = {
                "status": "skipped",
                "message": "No local node is registered; unable to enable features.",
            }
        elif not eligible and status == "error":
            enablement = {
                "status": "failed",
                "message": "Eligibility check failed; feature not enabled.",
            }

        if discovery_id:
            from apps.discovery.models import Discovery

            try:
                discovery = Discovery.objects.get(pk=discovery_id)
            except (Discovery.DoesNotExist, ValueError, TypeError):
                discovery = None
            if discovery:
                record_discovery_item(
                    discovery,
                    obj=feature,
                    label=str(feature.display),
                    created=assignment_created,
                    overwritten=not assignment_created and eligible,
                    data={
                        "eligible": eligible,
                        "status": status,
                        "message": message,
                        "enablement": enablement,
                        "level": level,
                    },
                )

        return JsonResponse(
            {
                "feature": feature.display,
                "slug": feature.slug,
                "status": status,
                "message": message,
                "eligible": eligible,
                "manual_enablement": manual_enablement,
                "enablement": enablement,
            }
        )

    def _report_prereq_checks(self, request, feature):
        from ..feature_checks import feature_checks

        result = feature_checks.run(feature, node=Node.get_local())
        if result:
            self.message_user(request, result.message, level=result.level)
