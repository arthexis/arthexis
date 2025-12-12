from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.contrib import admin, messages
from django.core.paginator import Paginator
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.camera import capture_rpi_snapshot
from apps.locals.user_data import EntityModelAdmin

from ..models import Node, NodeFeature
from ..reports import (
    collect_celery_log_entries,
    collect_scheduled_tasks,
    iter_report_periods,
    resolve_period,
)
from ..utils import (
    capture_screenshot,
    record_microphone_sample,
    save_audio_sample,
    save_screenshot,
)
from .forms import NodeFeatureAdminForm

@admin.register(NodeFeature)
class NodeFeatureAdmin(EntityModelAdmin):
    form = NodeFeatureAdminForm
    list_display = (
        "display",
        "slug",
        "default_roles",
        "is_enabled_display",
        "available_actions",
    )
    actions = ["check_features_for_eligibility", "enable_selected_features"]
    readonly_fields = ("is_enabled",)
    search_fields = ("display", "slug")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("roles")

    @admin.display(description="Default Roles")
    def default_roles(self, obj):
        roles = [role.name for role in obj.roles.all()]
        return ", ".join(roles) if roles else "—"

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

    @admin.action(description="Check features for eligibility")
    def check_features_for_eligibility(self, request, queryset):
        from ..feature_checks import feature_checks

        features = list(queryset)
        total = len(features)
        successes = 0
        node = Node.get_local()
        for feature in features:
            enablement_message = self._manual_enablement_message(feature, node)
            try:
                result = feature_checks.run(feature, node=node)
            except Exception as exc:  # pragma: no cover - defensive
                self.message_user(
                    request,
                    f"{feature.display}: {exc} {enablement_message}",
                    level=messages.ERROR,
                )
                continue
            if result is None:
                self.message_user(
                    request,
                    f"No check is configured for {feature.display}. {enablement_message}",
                    level=messages.WARNING,
                )
                continue
            message = result.message or (
                f"{feature.display} check {'passed' if result.success else 'failed'}."
            )
            self.message_user(
                request, f"{message} {enablement_message}", level=result.level
            )
            if result.success:
                successes += 1
        if total:
            self.message_user(
                request,
                f"Completed {successes} of {total} feature check(s) successfully.",
                level=messages.INFO,
            )

    @admin.action(description="Enable selected action")
    def enable_selected_features(self, request, queryset):
        node = Node.get_local()
        if node is None:
            self.message_user(
                request,
                "No local node is registered; unable to enable features manually.",
                level=messages.ERROR,
            )
            return

        manual_features = [
            feature
            for feature in queryset
            if feature.slug in Node.MANUAL_FEATURE_SLUGS
        ]
        non_manual_features = [
            feature
            for feature in queryset
            if feature.slug not in Node.MANUAL_FEATURE_SLUGS
        ]
        for feature in non_manual_features:
            self.message_user(
                request,
                f"{feature.display} cannot be enabled manually.",
                level=messages.WARNING,
            )

        if not manual_features:
            self.message_user(
                request,
                "None of the selected features can be enabled manually.",
                level=messages.WARNING,
            )
            return

        current_manual = set(
            node.features.filter(slug__in=Node.MANUAL_FEATURE_SLUGS).values_list(
                "slug", flat=True
            )
        )
        desired_manual = current_manual | {feature.slug for feature in manual_features}
        newly_enabled = desired_manual - current_manual
        if not newly_enabled:
            self.message_user(
                request,
                "Selected manual features are already enabled.",
                level=messages.INFO,
            )
            return

        node.update_manual_features(desired_manual)
        display_map = {feature.slug: feature.display for feature in manual_features}
        newly_enabled_names = [display_map[slug] for slug in sorted(newly_enabled)]
        self.message_user(
            request,
            "Enabled {} feature(s): {}".format(
                len(newly_enabled), ", ".join(newly_enabled_names)
            ),
            level=messages.SUCCESS,
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "celery-report/",
                self.admin_site.admin_view(self.celery_report),
                name="nodes_nodefeature_celery_report",
            ),
            path(
                "test-microphone/",
                self.admin_site.admin_view(self.test_microphone),
                name="nodes_nodefeature_test_microphone",
            ),
            path(
                "take-screenshot/",
                self.admin_site.admin_view(self.take_screenshot),
                name="nodes_nodefeature_take_screenshot",
            ),
            path(
                "take-snapshot/",
                self.admin_site.admin_view(self.take_snapshot),
                name="nodes_nodefeature_take_snapshot",
            ),
            path(
                "view-stream/",
                self.admin_site.admin_view(self.view_stream),
                name="nodes_nodefeature_view_stream",
            ),
        ]
        return custom + urls

    def celery_report(self, request):
        period = resolve_period(request.GET.get("period"))
        now = timezone.now()
        window_end = now + period.delta
        log_window_start = now - period.delta

        scheduled_tasks = collect_scheduled_tasks(now, window_end)
        log_collection = collect_celery_log_entries(log_window_start, now)

        log_paginator = Paginator(log_collection.entries, 100)
        log_page = log_paginator.get_page(request.GET.get("page"))
        query_params = request.GET.copy()
        if "page" in query_params:
            query_params.pop("page")
        base_query = query_params.urlencode()
        log_page_base = f"?{base_query}&page=" if base_query else "?page="

        period_options = [
            {
                "key": candidate.key,
                "label": candidate.label,
                "selected": candidate.key == period.key,
                "url": f"?period={candidate.key}",
            }
            for candidate in iter_report_periods()
        ]

        context = {
            **self.admin_site.each_context(request),
            "title": _("Celery Report"),
            "period": period,
            "period_options": period_options,
            "current_time": now,
            "window_end": window_end,
            "log_window_start": log_window_start,
            "scheduled_tasks": scheduled_tasks,
            "log_entries": list(log_page.object_list),
            "log_page": log_page,
            "log_paginator": log_paginator,
            "is_paginated": log_page.has_other_pages(),
            "log_page_base": log_page_base,
            "log_sources": log_collection.checked_sources,
        }
        return TemplateResponse(
            request,
            "admin/nodes/nodefeature/celery_report.html",
            context,
        )

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

    def test_microphone(self, request):
        feature = self._ensure_feature_enabled(
            request, "audio-capture", "Test Microphone"
        )
        if not feature:
            return redirect("..")

        if not Node._has_audio_capture_device():
            self.message_user(
                request,
                "Audio Capture feature is enabled but no recording device was detected.",
                level=messages.ERROR,
            )
            return redirect("..")

        try:
            path = record_microphone_sample(duration_seconds=6)
        except Exception as exc:  # pragma: no cover - depends on system audio
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")

        node = Node.get_local()
        sample = save_audio_sample(path, node=node, method="DEFAULT_ACTION")
        if not sample:
            self.message_user(
                request, "Duplicate audio sample; not saved", level=messages.INFO
            )
            return redirect("..")

        self.message_user(
            request, f"Audio sample saved to {sample.path}", level=messages.SUCCESS
        )
        try:
            change_url = reverse(
                "admin:nodes_contentsample_change", args=[sample.pk]
            )
        except NoReverseMatch:  # pragma: no cover - admin URL always registered
            self.message_user(
                request,
                "Audio sample saved but the admin page could not be resolved.",
                level=messages.WARNING,
            )
            return redirect("..")
        return redirect(change_url)

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
                "admin:nodes_contentsample_change", args=[sample.pk]
            )
        except NoReverseMatch:  # pragma: no cover - admin URL always registered
            self.message_user(
                request,
                "Screenshot saved but the admin page could not be resolved.",
                level=messages.WARNING,
            )
            return redirect("..")
        return redirect(change_url)

    def take_snapshot(self, request):
        feature = self._ensure_feature_enabled(
            request, "rpi-camera", "Take a Snapshot"
        )
        if not feature:
            return redirect("..")
        try:
            path = capture_rpi_snapshot()
        except Exception as exc:  # pragma: no cover - depends on camera stack
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")
        node = Node.get_local()
        sample = save_screenshot(path, node=node, method="RPI_CAMERA")
        if not sample:
            self.message_user(
                request, "Duplicate snapshot; not saved", level=messages.INFO
            )
            return redirect("..")
        self.message_user(
            request, f"Snapshot saved to {sample.path}", level=messages.SUCCESS
        )
        try:
            change_url = reverse(
                "admin:nodes_contentsample_change", args=[sample.pk]
            )
        except NoReverseMatch:  # pragma: no cover - admin URL always registered
            self.message_user(
                request,
                "Snapshot saved but the admin page could not be resolved.",
                level=messages.WARNING,
            )
            return redirect("..")
        return redirect(change_url)

    def view_stream(self, request):
        feature = self._ensure_feature_enabled(request, "rpi-camera", "View stream")
        if not feature:
            return redirect("..")

        configured_stream = getattr(settings, "RPI_CAMERA_STREAM_URL", "").strip()
        if configured_stream:
            stream_url = configured_stream
        else:
            base_uri = request.build_absolute_uri("/")
            parsed = urlsplit(base_uri)
            hostname = parsed.hostname or "127.0.0.1"
            port = getattr(settings, "RPI_CAMERA_STREAM_PORT", 8554)
            scheme = getattr(settings, "RPI_CAMERA_STREAM_SCHEME", "http")
            netloc = f"{hostname}:{port}" if port else hostname
            stream_url = urlunsplit((scheme, netloc, "/", "", ""))
        parsed_stream = urlsplit(stream_url)
        path = (parsed_stream.path or "").lower()
        query = (parsed_stream.query or "").lower()

        if parsed_stream.scheme in {"rtsp", "rtsps"}:
            embed_mode = "unsupported"
        elif any(path.endswith(ext) for ext in (".mjpg", ".mjpeg", ".jpeg", ".jpg", ".png")) or "action=stream" in query:
            embed_mode = "mjpeg"
        else:
            embed_mode = "iframe"

        context = {
            **self.admin_site.each_context(request),
            "title": _("Raspberry Pi Camera Stream"),
            "stream_url": stream_url,
            "stream_embed": embed_mode,
        }
        return TemplateResponse(
            request,
            "admin/nodes/nodefeature/view_stream.html",
            context,
        )

