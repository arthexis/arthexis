from __future__ import annotations

import base64
from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import NoReverseMatch, path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from parler.admin import TranslatableAdmin

from apps.audio.models import AudioSample
from apps.content.models import (
    ContentClassification,
    ContentClassifier,
    ContentSample,
    ContentTag,
    WebSample,
    WebSampleAttachment,
)
from apps.content.upload_handlers import MaxContentDropUploadSizeHandler
from apps.content.utils import (
    capture_screenshot,
    create_uploaded_content_sample,
    get_max_content_drop_size,
    save_screenshot,
)
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node, NodeFeature
from apps.video.models import VideoDevice
from apps.video.utils import DEFAULT_CAMERA_RESOLUTION, capture_rpi_snapshot

@admin.register(ContentTag)
class ContentTagAdmin(TranslatableAdmin, EntityModelAdmin):
    list_display = ("label", "slug")
    search_fields = ("label", "slug")


@admin.register(ContentClassifier)
class ContentClassifierAdmin(TranslatableAdmin, EntityModelAdmin):
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
    readonly_fields = ("created_at", "name", "user", "image_preview", "audio_preview")
    inlines = (ContentClassificationInline,)
    list_filter = ("kind", "classifications__tag")
    change_form_template = "admin/content/contentsample/change_form.html"

    @staticmethod
    def _resolve_sample_path(sample: ContentSample) -> Path:
        file_path = Path(sample.path)
        if not file_path.is_absolute():
            file_path = settings.LOG_DIR / file_path
        return file_path

    @staticmethod
    def _wants_json_response(request) -> bool:
        """Return whether the caller expects a JSON payload instead of a redirect."""

        accept = request.headers.get("Accept", "")
        return (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in accept
        )

    def _get_sample_preview(self, obj: ContentSample | None) -> str | None:
        if not obj or obj.kind != ContentSample.IMAGE or not obj.path:
            return None
        file_path = self._resolve_sample_path(obj)
        if not file_path.exists():
            return None
        try:
            encoded = base64.b64encode(file_path.read_bytes())
        except OSError:
            return None
        return encoded.decode("ascii")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "capture/",
                self.admin_site.admin_view(self.capture_now),
                name="nodes_contentsample_capture",
            ),
            path(
                "drop-upload/",
                self.admin_site.admin_view(csrf_exempt(self.drop_upload)),
                name="content_contentsample_drop_upload",
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

    def drop_upload(self, request):
        """Create a content sample from a drag-and-drop upload and return its admin URL."""

        request.upload_handlers = [
            MaxContentDropUploadSizeHandler(
                request,
                max_size=get_max_content_drop_size(),
            ),
            *request.upload_handlers,
        ]

        @csrf_protect
        def protected_view(inner_request):
            return self._drop_upload(inner_request)

        return protected_view(request)

    def _drop_upload(self, request):
        """Handle a validated admin drag-and-drop upload after CSRF protection."""

        if request.method != "POST":
            raise PermissionDenied
        if not self.has_add_permission(request):
            raise PermissionDenied

        uploaded_file = request.FILES.get("file")
        upload_error = getattr(request, "content_drop_upload_error", None)
        if upload_error is not None:
            return self._handle_drop_upload_error(request, upload_error)
        if uploaded_file is None:
            return self._handle_drop_upload_error(
                request,
                ValidationError(_("No file was uploaded.")),
            )

        try:
            sample = create_uploaded_content_sample(
                uploaded_file=uploaded_file,
                user=request.user,
            )
        except ValidationError as exc:
            return self._handle_drop_upload_error(request, exc)
        change_url = reverse("admin:content_contentsample_change", args=[sample.pk])

        if self._wants_json_response(request):
            return JsonResponse({"change_url": change_url, "sample_id": sample.pk}, status=201)

        self.message_user(
            request,
            format_html(
                '{} <a href="{}">{}</a>',
                _("Content sample uploaded."),
                change_url,
                _("View sample"),
            ),
            level=messages.SUCCESS,
        )
        return redirect(change_url)

    def _handle_drop_upload_error(self, request, error: ValidationError):
        """Return the standard JSON-or-redirect response for invalid uploads.

        Parameters:
            request: Active admin request.
            error: Validation failure describing why the upload was rejected.

        Returns:
            A JSON 400 response for XHR callers or an admin redirect with a
            flashed error message.
        """

        message = error.messages[0] if error.messages else _("The uploaded file is invalid.")
        response = JsonResponse({"error": message}, status=400)
        if self._wants_json_response(request):
            return response
        self.message_user(request, message, level=messages.ERROR)
        return redirect(request.META.get("HTTP_REFERER", reverse("admin:index")))

    def take_snapshot(self, request, object_id):
        del object_id
        if request.method != "POST":
            raise PermissionDenied

        if not self.has_add_permission(request):
            raise PermissionDenied

        node = Node.get_local()
        if node is None:
            self.message_user(
                request,
                _("No local node is registered; cannot perform camera actions."),
                level=messages.ERROR,
            )
            return redirect("..")

        video_feature = NodeFeature.objects.filter(slug="video-cam").first()
        if not video_feature or not video_feature.is_enabled:
            self.message_user(
                request,
                _("Video Camera feature is not enabled on this node."),
                level=messages.WARNING,
            )
            return redirect("..")

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

        try:
            change_url = reverse("admin:content_contentsample_change", args=[sample.pk])
        except NoReverseMatch:  # pragma: no cover - admin URL always registered
            self.message_user(
                request,
                _("Snapshot saved to %(path)s") % {"path": sample.path},
                messages.SUCCESS,
            )
            self.message_user(
                request,
                _("Snapshot saved but the admin page could not be resolved."),
                level=messages.WARNING,
            )
            return redirect("..")

        self.message_user(
            request,
            format_html(
                '{} <a href="{}">{}</a>',
                _("Snapshot saved."),
                change_url,
                _("View sample"),
            ),
            messages.SUCCESS,
        )
        return redirect(change_url)

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

    @admin.display(description="Audio")
    def audio_preview(self, obj):
        if not obj or obj.kind != ContentSample.AUDIO or not obj.path:
            return ""
        audio_sample = obj.audio_samples.order_by("-captured_at", "-id").first()
        if not audio_sample:
            return "Audio metadata not available"
        data_uri = audio_sample.get_data_uri()
        if not data_uri:
            return "File not found"
        return format_html(
            '<audio controls style="width:100%%;" src="{}"></audio>',
            data_uri,
        )


class WebSampleAttachmentInline(admin.TabularInline):
    model = WebSampleAttachment
    extra = 0
    readonly_fields = (
        "content_sample",
        "legacy_step_id",
        "step_slug",
        "step_name",
        "uri",
    )


@admin.register(WebSample)
class WebSampleAdmin(EntityModelAdmin):
    list_display = ("sampler_label", "sampler_slug", "executed_by", "created_at")
    readonly_fields = (
        "legacy_sampler_id",
        "sampler_slug",
        "sampler_label",
        "document",
        "executed_by",
        "created_at",
    )
    inlines = (WebSampleAttachmentInline,)
