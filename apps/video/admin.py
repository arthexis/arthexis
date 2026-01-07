import base64
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from PIL import Image
from django_object_actions import DjangoObjectActions

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.nodes.utils import save_screenshot

from .models import (
    MjpegStream,
    VideoDevice,
    VideoDeviceSnapshot,
    VideoRecording,
    YoutubeChannel,
)
from .utils import capture_rpi_snapshot, has_rpi_camera_stack


@admin.register(VideoDevice)
class VideoDeviceAdmin(OwnableAdminMixin, DjangoObjectActions, EntityModelAdmin):
    list_display = ("identifier", "node", "owner_display", "is_default", "visibility")
    search_fields = ("identifier", "description", "raw_info", "node__hostname")
    changelist_actions = ["find_video_devices", "take_snapshot", "test_camera"]
    change_actions = ["take_snapshot_action"]
    change_list_template = "django_object_actions/change_list.html"
    readonly_fields = ("latest_snapshot",)
    fieldsets = (
        (None, {"fields": ("node", "identifier", "description", "raw_info", "is_default")}),
        (_("Ownership"), {"fields": ("user", "group")}),
        (_("Latest"), {"fields": ("latest_snapshot",)}),
    )

    def get_urls(self):
        custom = [
            path(
                "find-video-devices/",
                self.admin_site.admin_view(self.find_video_devices_view),
                name="video_videodevice_find_devices",
            ),
            path(
                "take-snapshot/",
                self.admin_site.admin_view(self.take_snapshot_view),
                name="video_videodevice_take_snapshot",
            ),
            path(
                "view-stream/",
                self.admin_site.admin_view(self.view_stream),
                name="video_videodevice_view_stream",
            ),
            path(
                "test-camera/",
                self.admin_site.admin_view(self.view_stream),
                name="video_videodevice_test_camera",
            ),
        ]
        return custom + super().get_urls()

    def find_video_devices(self, request, queryset=None):
        return redirect("admin:video_videodevice_find_devices")

    def take_snapshot(self, request, queryset=None):
        return redirect("admin:video_videodevice_take_snapshot")

    def take_snapshot_action(self, request, obj):
        sample = self._capture_snapshot_for_device(request, obj)
        if sample:
            try:
                return redirect(
                    reverse("admin:content_contentsample_change", args=[sample.pk])
                )
            except NoReverseMatch:  # pragma: no cover - admin always registered
                pass
        return redirect(request.path)

    def test_camera(self, request, queryset=None):
        return redirect("admin:video_videodevice_view_stream")

    find_video_devices.label = _("Find Video Devices")
    find_video_devices.short_description = _("Find Video Devices")
    find_video_devices.changelist = True

    take_snapshot.label = _("Take Snapshot")
    take_snapshot.short_description = _("Take Snapshot")
    take_snapshot.changelist = True

    take_snapshot_action.label = _("Take Snapshot")
    take_snapshot_action.short_description = _("Refresh snapshot")

    test_camera.label = _("Test Camera")
    test_camera.short_description = _("Test Camera")
    test_camera.changelist = True

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "GET" and object_id:
            obj = self.get_object(request, object_id)
            if obj and not obj.get_latest_snapshot():
                self._capture_snapshot_for_device(
                    request,
                    obj,
                    silent=True,
                )
        return super().changeform_view(request, object_id, form_url, extra_context)

    def _ensure_video_feature_enabled(
        self,
        request,
        action_label: str,
        *,
        auto_enable: bool = False,
        require_stack: bool = True,
    ):
        try:
            feature = NodeFeature.objects.get(slug="rpi-camera")
        except NodeFeature.DoesNotExist:
            self.message_user(
                request,
                _("%(action)s is unavailable because the feature is not configured.")
                % {"action": action_label},
                level=messages.ERROR,
            )
            return None
        if feature.is_enabled:
            return feature

        node = Node.get_local()
        if auto_enable and node:
            if require_stack and not has_rpi_camera_stack():
                self.message_user(
                    request,
                    _("%(feature)s feature is not enabled on this node.")
                    % {"feature": feature.display},
                    level=messages.WARNING,
                )
                return None

            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
            return feature

        self.message_user(
            request,
            _("%(feature)s feature is not enabled on this node.")
            % {"feature": feature.display},
            level=messages.WARNING,
        )
        return None

    def _get_local_node(self, request):
        node = Node.get_local()
        if node is None:
            self.message_user(
                request,
                _("No local node is registered; cannot perform video actions."),
                level=messages.ERROR,
            )
        return node

    @admin.display(description=_("Visibility"))
    def visibility(self, obj):
        if not obj:
            return ""
        return _("Public") if obj.is_public else obj.owner_display()

    @admin.display(description=_("Latest snapshot"))
    def latest_snapshot(self, obj):
        if not obj:
            return ""
        sample = obj.get_latest_snapshot()
        if not sample:
            return _("No snapshots captured yet.")

        file_path = Path(sample.path)
        if not file_path.is_absolute():
            file_path = settings.LOG_DIR / file_path

        metadata: list[str] = []
        timestamp = timezone.localtime(sample.created_at)
        metadata.append(
            _("Captured at %(timestamp)s")
            % {"timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S %Z")}
        )

        image_html = ""
        mime_type = "image/jpeg"

        if file_path.exists():
            try:
                with Image.open(file_path) as image:
                    width, height = image.size
                    fmt = image.format or "JPEG"
                    mime_type = f"image/{fmt.lower()}"
                    metadata.append(
                        _("Resolution: %(width)s×%(height)s")
                        % {"width": width, "height": height}
                    )
                    metadata.append(_("Format: %(format)s") % {"format": fmt})
                with file_path.open("rb") as fh:
                    encoded = base64.b64encode(fh.read()).decode("ascii")
                image_html = format_html(
                    '<div style="margin-bottom:8px;"><img src="data:{};base64,{}" '
                    'style="max-width:100%; max-height:400px;" /></div>',
                    mime_type,
                    encoded,
                )
            except Exception:
                metadata.append(_("Snapshot could not be displayed."))
        else:
            metadata.append(_("Snapshot file missing at %(path)s") % {"path": file_path})

        metadata_html = mark_safe("<br />".join(metadata))
        return format_html("{}{}", image_html, metadata_html)

    def _capture_snapshot_for_device(self, request, device, *, silent: bool = False):
        feature = self._ensure_video_feature_enabled(
            request, _("Take Snapshot"), auto_enable=True
        )
        if not feature:
            return None

        node = device.node or self._get_local_node(request)
        if node is None:
            return None

        NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)

        try:
            path = capture_rpi_snapshot()
        except Exception as exc:  # pragma: no cover - depends on camera stack
            if not silent:
                self.message_user(request, str(exc), level=messages.ERROR)
            return None

        sample = save_screenshot(
            path,
            node=node,
            method="RPI_CAMERA",
            link_duplicates=True,
        )

        if not sample:
            if not silent:
                self.message_user(
                    request, _("Duplicate snapshot; not saved"), level=messages.INFO
                )
            return None

        VideoDeviceSnapshot.objects.update_or_create(
            video_device=device, sample=sample
        )
        device.link_snapshot(sample)

        if not silent:
            self.message_user(
                request,
                _("Snapshot saved to %(path)s") % {"path": sample.path},
                level=messages.SUCCESS,
            )
        return sample

    def find_video_devices_view(self, request):
        feature = self._ensure_video_feature_enabled(
            request, _("Find Video Devices"), auto_enable=True, require_stack=False
        )
        if not feature:
            return redirect("..")

        node = self._get_local_node(request)
        if node is None:
            return redirect("..")

        if not has_rpi_camera_stack():
            self.message_user(
                request,
                _("No video devices were detected on this node."),
                level=messages.WARNING,
            )
            return redirect("..")

        created, updated = VideoDevice.refresh_from_system(node=node)

        NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)

        if created or updated:
            self.message_user(
                request,
                _("Updated %(created)s new and %(updated)s existing video devices.")
                % {"created": created, "updated": updated},
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("No video devices were added or updated."),
                level=messages.INFO,
            )
        return redirect("..")

    def take_snapshot_view(self, request):
        feature = self._ensure_video_feature_enabled(
            request, _("Take a Snapshot"), auto_enable=True
        )
        if not feature:
            return redirect("..")
        node = self._get_local_node(request)
        if node is None:
            return redirect("..")
        if not VideoDevice.objects.filter(node=node).exists():
            VideoDevice.refresh_from_system(node=node)
        if not VideoDevice.objects.filter(node=node).exists():
            self.message_user(
                request,
                _("No video devices were detected on this node."),
                level=messages.WARNING,
            )
            return redirect("..")
        try:
            path = capture_rpi_snapshot()
        except Exception as exc:  # pragma: no cover - depends on camera stack
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")
        sample = save_screenshot(
            path,
            node=node,
            method="RPI_CAMERA",
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
            level=messages.SUCCESS,
        )
        try:
            change_url = reverse("admin:content_contentsample_change", args=[sample.pk])
        except NoReverseMatch:  # pragma: no cover - admin URL always registered
            self.message_user(
                request,
                _("Snapshot saved but the admin page could not be resolved."),
                level=messages.WARNING,
            )
            return redirect("..")
        return redirect(change_url)

    def view_stream(self, request):
        feature = self._ensure_video_feature_enabled(
            request, _("View stream"), auto_enable=True
        )
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
        elif any(
            path.endswith(ext)
            for ext in (".mjpg", ".mjpeg", ".jpeg", ".jpg", ".png")
        ) or "action=stream" in query:
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
            "admin/video/view_stream.html",
            context,
        )


@admin.register(VideoRecording)
class VideoRecordingAdmin(EntityModelAdmin):
    list_display = ("node", "path", "duration_seconds", "recorded_at", "method")
    search_fields = ("path", "node__hostname", "method")
    readonly_fields = ("recorded_at",)


@admin.register(MjpegStream)
class MjpegStreamAdmin(EntityModelAdmin):
    list_display = ("name", "slug", "video_device", "is_active", "public_link")
    search_fields = ("name", "slug", "video_device__identifier")
    list_filter = ("is_active",)

    def get_view_on_site_url(self, obj=None):
        if obj:
            return obj.get_absolute_url()
        return super().get_view_on_site_url(obj)

    @admin.display(description=_("Public link"))
    def public_link(self, obj):
        if not obj:
            return ""
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            obj.get_absolute_url(),
            _("View"),
        )


@admin.register(YoutubeChannel)
class YoutubeChannelAdmin(EntityModelAdmin):
    list_display = ("title", "handle_display", "channel_id", "channel_url")
    search_fields = ("title", "channel_id", "handle", "description")
    readonly_fields = ("channel_url",)

    @admin.display(description=_("Handle"))
    def handle_display(self, obj):
        return obj.get_handle(include_at=True)

    @admin.display(description=_("Channel URL"))
    def channel_url(self, obj):
        return obj.get_channel_url()
