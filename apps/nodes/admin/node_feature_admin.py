from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import NoReverseMatch, path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from ..models import Node, NodeFeature
from apps.content.utils import capture_screenshot, save_screenshot
from .actions import check_features_for_eligibility, enable_selected_features
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
    actions = [check_features_for_eligibility, enable_selected_features]
    readonly_fields = ("is_enabled",)
    search_fields = ("display", "slug")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("roles")

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

    def _manual_enablement_message(self, feature, node):
        if node is None:
            return (
                "Manual enablement is unavailable without a registered local node."
            )
        if feature.slug in Node.MANUAL_FEATURE_SLUGS:
            return "This feature can be enabled manually."
        return "This feature cannot be enabled manually."

    def get_urls(self):
        urls = super().get_urls()
        custom = [
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

    def _report_prereq_checks(self, request, feature):
        from ..feature_checks import feature_checks

        result = feature_checks.run(feature, node=Node.get_local())
        if result:
            self.message_user(request, result.message, level=result.level)
