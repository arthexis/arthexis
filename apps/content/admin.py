import base64
from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.content.models import (
    ContentClassification,
    ContentClassifier,
    ContentSample,
    ContentTag,
    WebRequestSampler,
    WebRequestStep,
    WebSample,
    WebSampleAttachment,
)
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node
from apps.nodes.utils import capture_screenshot, save_screenshot
from apps.video.models import VideoDevice
from apps.video.utils import (
    DEFAULT_CAMERA_RESOLUTION,
    capture_rpi_snapshot,
    has_rpi_camera_stack,
)
from .web_sampling import execute_sampler


@admin.register(ContentTag)
class ContentTagAdmin(EntityModelAdmin):
    list_display = ("label", "slug")
    search_fields = ("label", "slug")


@admin.register(ContentClassifier)
class ContentClassifierAdmin(EntityModelAdmin):
    list_display = ("label", "slug", "kind", "run_by_default", "active")
    list_filter = ("kind", "run_by_default", "active")
    search_fields = ("label", "slug", "entrypoint")


class ContentClassificationInline(admin.TabularInline):
    model = ContentClassification
    extra = 0
    autocomplete_fields = ("classifier", "tag")


@admin.register(ContentSample)
class ContentSampleAdmin(EntityModelAdmin):
    list_display = ("name", "kind", "node", "user", "created_at")
    readonly_fields = ("created_at", "name", "user", "image_preview")
    inlines = (ContentClassificationInline,)
    list_filter = ("kind", "classifications__tag")
    change_form_template = "admin/content/contentsample/change_form.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "capture/",
                self.admin_site.admin_view(self.capture_now),
                name="nodes_contentsample_capture",
            ),
            path(
                "<path:object_id>/take-snapshot/",
                self.admin_site.admin_view(self.take_snapshot),
                name="content_contentsample_take_snapshot",
            ),
        ]
        return custom + urls

    def capture_now(self, request):
        node = Node.get_local()
        url = request.build_absolute_uri("/")
        try:
            path = capture_screenshot(url)
        except Exception as exc:  # pragma: no cover - depends on selenium setup
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")
        sample = save_screenshot(path, node=node, method="ADMIN")
        if sample:
            self.message_user(request, f"Screenshot saved to {path}", messages.SUCCESS)
        else:
            self.message_user(request, "Duplicate screenshot; not saved", messages.INFO)
        return redirect("..")

    def take_snapshot(self, request, _object_id):
        if not has_rpi_camera_stack():
            self.message_user(
                request,
                _("Camera stack not available."),
                level=messages.ERROR,
            )
            return redirect("..")

        node = Node.get_local()
        device = VideoDevice.objects.filter(node=node, is_default=True).first()
        width = getattr(device, "capture_width", None)
        height = getattr(device, "capture_height", None)
        if not width or not height:
            width, height = DEFAULT_CAMERA_RESOLUTION

        try:
            path = capture_rpi_snapshot(width=width, height=height)
        except Exception as exc:  # pragma: no cover - depends on camera stack
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")

        sample = save_screenshot(
            path,
            node=node,
            method="RPI_CAMERA",
            user=request.user,
            link_duplicates=True,
        )
        if not sample:
            self.message_user(
                request, _("Duplicate snapshot; not saved"), level=messages.INFO
            )
            return redirect("..")

        self.message_user(
            request,
            _("Snapshot saved to %(path)s") % {"path": sample.path},
            messages.SUCCESS,
        )
        return redirect(
            reverse("admin:content_contentsample_change", args=[sample.pk])
        )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id) if object_id else None
        extra_context["latest_sample"] = obj
        extra_context["latest_preview"] = self._get_sample_preview(obj)
        extra_context["take_snapshot_url"] = (
            reverse("admin:content_contentsample_take_snapshot", args=[obj.pk])
            if obj
            else None
        )
        return super().changeform_view(
            request, object_id, form_url=form_url, extra_context=extra_context
        )

    @admin.display(description="Screenshot")
    def image_preview(self, obj):
        if not obj or obj.kind != ContentSample.IMAGE or not obj.path:
            return ""
        encoded = self._get_sample_preview(obj)
        if not encoded:
            return "File not found"
        return format_html(
            '<img src="data:image/png;base64,{}" style="max-width:100%;" />',
            encoded,
        )

    def _get_sample_preview(self, obj):
        if not obj or obj.kind != ContentSample.IMAGE or not obj.path:
            return None
        file_path = Path(obj.path)
        if not file_path.is_absolute():
            file_path = settings.LOG_DIR / file_path
        if not file_path.exists():
            return None
        with file_path.open("rb") as f:
            return base64.b64encode(f.read()).decode("ascii")


class WebRequestStepInline(admin.TabularInline):
    model = WebRequestStep
    extra = 0
    fields = (
        "order",
        "slug",
        "name",
        "curl_command",
        "save_as_content",
        "attachment_kind",
    )


@admin.register(WebRequestSampler)
class WebRequestSamplerAdmin(EntityModelAdmin):
    list_display = (
        "label",
        "slug",
        "sampling_period_minutes",
        "last_sampled_at",
        "owner",
        "security_group",
    )
    search_fields = ("label", "slug", "description")
    list_filter = ("sampling_period_minutes", "owner", "security_group")
    actions = ("execute_selected_samplers",)
    inlines = (WebRequestStepInline,)

    @admin.action(description="Execute selected Samplers")
    def execute_selected_samplers(self, request, queryset):
        executed = 0
        for sampler in queryset:
            try:
                execute_sampler(sampler, user=request.user)
                executed += 1
            except Exception as exc:  # pragma: no cover - admin message only
                self.message_user(request, str(exc), level=messages.ERROR)
        if executed:
            self.message_user(
                request,
                f"Executed {executed} sampler(s)",
                level=messages.SUCCESS,
            )


class WebSampleAttachmentInline(admin.TabularInline):
    model = WebSampleAttachment
    extra = 0
    readonly_fields = ("content_sample", "uri", "step")


@admin.register(WebSample)
class WebSampleAdmin(EntityModelAdmin):
    list_display = ("sampler", "executed_by", "created_at")
    readonly_fields = ("document", "executed_by", "created_at")
    inlines = (WebSampleAttachmentInline,)
