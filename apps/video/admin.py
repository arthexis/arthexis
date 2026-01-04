from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from django_object_actions import DjangoObjectActions

from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.nodes.utils import save_screenshot

from .models import MjpegStream, VideoDevice, VideoRecording, YoutubeChannel
from .utils import capture_rpi_snapshot, has_rpi_camera_stack


@admin.register(VideoDevice)
class VideoDeviceAdmin(DjangoObjectActions, EntityModelAdmin):
    list_display = ("identifier", "node", "description", "is_default")
    search_fields = ("identifier", "description", "raw_info", "node__hostname")
    changelist_actions = ["find_video_devices", "take_snapshot", "test_camera"]
    change_list_template = "django_object_actions/change_list.html"

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

    def test_camera(self, request, queryset=None):
        return redirect("admin:video_videodevice_view_stream")

    find_video_devices.label = _("Find Video Devices")
    find_video_devices.short_description = _("Find Video Devices")
    find_video_devices.changelist = True

    take_snapshot.label = _("Take Snapshot")
    take_snapshot.short_description = _("Take Snapshot")
    take_snapshot.changelist = True

    test_camera.label = _("Test Camera")
    test_camera.short_description = _("Test Camera")
    test_camera.changelist = True

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
