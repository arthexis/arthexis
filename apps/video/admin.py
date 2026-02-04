from urllib.parse import urlsplit, urlunsplit

import requests
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from django.utils.text import slugify
from django_object_actions import DjangoObjectActions

from apps.discovery.services import record_discovery_item, start_discovery
from apps.core.admin.mixins import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment

from .models import (
    MjpegDependencyError,
    MjpegDeviceUnavailableError,
    MjpegStream,
    VideoDevice,
    VideoRecording,
    VideoSnapshot,
    YoutubeChannel,
)
from .utils import (
    DEFAULT_CAMERA_RESOLUTION,
    get_camera_resolutions,
    has_rpi_camera_stack,
)


def set_admin_action_label(action, label, *, changelist=False):
    action.label = _(label)
    action.short_description = action.label
    if changelist:
        action.changelist = True
    if label == "Discover":
        action.is_discover_action = True


class VideoDeviceAdminForm(forms.ModelForm):
    resolution_choice = forms.ChoiceField(
        required=False,
        label=_("Resolution"),
        help_text=_("Choose a supported resolution or enter a custom width and height."),
    )

    class Meta:
        model = VideoDevice
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        resolutions = get_camera_resolutions()
        default_width, default_height = DEFAULT_CAMERA_RESOLUTION
        choices = [
            (
                "",
                _("Default (%(width)s × %(height)s)")
                % {"width": default_width, "height": default_height},
            )
        ]
        choices.extend(
            (f"{width}x{height}", f"{width} × {height}") for width, height in resolutions
        )
        self.fields["resolution_choice"].choices = choices

        if self.instance and self.instance.pk:
            width = self.instance.capture_width
            height = self.instance.capture_height
            if width and height:
                self.fields["resolution_choice"].initial = f"{width}x{height}"
        if not self.initial.get("capture_width") and not self.initial.get(
            "capture_height"
        ):
            self.initial.setdefault("capture_width", default_width)
            self.initial.setdefault("capture_height", default_height)

    def clean(self):
        cleaned_data = super().clean()
        choice = cleaned_data.get("resolution_choice")
        default_width, default_height = DEFAULT_CAMERA_RESOLUTION

        if choice:
            try:
                width_str, height_str = choice.lower().split("x", 1)
                cleaned_data["capture_width"] = int(width_str)
                cleaned_data["capture_height"] = int(height_str)
                return cleaned_data
            except (ValueError, AttributeError):
                self.add_error(
                    "resolution_choice", _("Select a valid resolution option.")
                )

        width = cleaned_data.get("capture_width")
        height = cleaned_data.get("capture_height")
        if (width and not height) or (height and not width):
            self.add_error(
                None,
                forms.ValidationError(
                    _(
                        "Both capture width and height must be provided together, or both left blank to use the default."
                    ),
                    code="incomplete_resolution",
                ),
            )
        elif not width and not height:
            cleaned_data["capture_width"] = default_width
            cleaned_data["capture_height"] = default_height
        return cleaned_data


@admin.register(VideoDevice)
class VideoDeviceAdmin(DjangoObjectActions, OwnableAdminMixin, EntityModelAdmin):
    form = VideoDeviceAdminForm
    view_on_site = True
    list_display = (
        "name",
        "slug",
        "node",
        "owner_display",
        "description",
        "is_default",
    )
    search_fields = (
        "name",
        "slug",
        "identifier",
        "description",
        "raw_info",
        "node__hostname",
    )
    actions = ("reload_resolution_defaults",)
    changelist_actions = [
        "find_devices",
        "take_snapshot",
    ]
    change_list_template = "django_object_actions/change_list.html"
    change_form_template = "admin/video/videodevice/change_form.html"
    change_actions = ("refresh_snapshot", "goto_stream")
    fieldsets = (
        (
            None,
            {
                    "fields": (
                        "node",
                        "name",
                        "slug",
                        "identifier",
                        "description",
                        "raw_info",
                        "is_default",
                )
            },
        ),
        (
            _("Camera Resolution"),
            {
                "fields": (
                    "resolution_choice",
                    "capture_width",
                    "capture_height",
                    "auto_rotate",
                )
            },
        ),
    )

    def get_urls(self):
        custom = [
            path(
                "find-devices/",
                self.admin_site.admin_view(self.find_devices_view),
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
        ]
        return custom + super().get_urls()

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id) if object_id else None
        latest_snapshot = obj.get_latest_snapshot() if obj else None
        if obj and obj.pk and latest_snapshot is None:
            latest_snapshot = self._capture_snapshot_for_device(
                request,
                obj,
                auto_enable=True,
                link_duplicates=True,
                silent=True,
            ) or obj.get_latest_snapshot()
        extra_context["latest_snapshot"] = latest_snapshot
        if latest_snapshot:
            try:
                extra_context["latest_snapshot_sample_url"] = reverse(
                    "admin:content_contentsample_change",
                    args=[latest_snapshot.sample_id],
                )
            except NoReverseMatch:
                extra_context["latest_snapshot_sample_url"] = None
        if obj:
            extra_context["mjpeg_streams"] = [
                {
                    "stream": stream,
                    "admin_url": stream.get_admin_url(),
                    "public_url": stream.get_stream_url(),
                    "last_snapshot_at": stream.last_frame_captured_at
                    or stream.last_thumbnail_at,
                }
                for stream in obj.mjpeg_streams.order_by("name", "pk")
            ]
        return super().changeform_view(
            request, object_id, form_url=form_url, extra_context=extra_context
        )

    def get_view_on_site_url(self, obj=None):
        if not obj:
            return None
        stream = obj.mjpeg_streams.order_by("-is_active", "pk").first()
        if stream:
            return stream.get_absolute_url()
        try:
            return reverse("video:camera-gallery")
        except NoReverseMatch:
            return None

    def find_devices(self, request, queryset=None):
        return self._redirect_admin("video_videodevice_find_devices")

    def take_snapshot(self, request, queryset=None):
        return self._redirect_admin("video_videodevice_take_snapshot")

    def refresh_snapshot(self, request, obj):
        self._capture_snapshot_for_device(
            request, obj, auto_enable=True, link_duplicates=True
        )
        return redirect(
            reverse(f"admin:{self._admin_view_name('change')}", args=[obj.pk])
        )

    def goto_stream(self, request, obj):
        stream = obj.mjpeg_streams.order_by("-is_active", "pk").first()
        if stream is None:
            stream = self._create_default_stream(obj)
            self.message_user(
                request,
                _("Created MJPEG stream %(name)s.") % {"name": stream.name},
                level=messages.SUCCESS,
            )
        return redirect(stream.get_admin_url())

    @admin.action(description=_("Reload resolution defaults"))
    def reload_resolution_defaults(self, request, queryset):
        width, height = DEFAULT_CAMERA_RESOLUTION
        updated = queryset.update(capture_width=width, capture_height=height)
        if updated:
            self.message_user(
                request,
                _("Updated %(count)s device(s) with default resolution.")
                % {"count": updated},
                level=messages.SUCCESS,
            )

    set_admin_action_label(find_devices, "Discover", changelist=True)
    set_admin_action_label(take_snapshot, "Take Snapshot", changelist=True)
    set_admin_action_label(refresh_snapshot, "Snapshot")
    set_admin_action_label(goto_stream, "Goto Stream")

    def _create_default_stream(self, device: VideoDevice) -> MjpegStream:
        slug_field = MjpegStream._meta.get_field("slug")
        max_length = slug_field.max_length or 50
        base_slug = slugify(device.slug or device.name or f"device-{device.pk}") or (
            f"device-{device.pk}"
        )
        base_slug = base_slug[:max_length].rstrip("-") or base_slug[:max_length]
        if not base_slug:
            base_slug = f"device-{device.pk}"[:max_length]
        prefix = base_slug
        if max_length > 2:
            prefix = base_slug[: max_length - 2].rstrip("-") or base_slug
        existing_slugs = set(
            MjpegStream.objects.filter(slug__startswith=prefix).values_list(
                "slug", flat=True
            )
        )

        def build_slug(counter: int) -> str:
            if counter <= 1:
                return base_slug
            suffix = f"-{counter}"
            available = max_length - len(suffix)
            trimmed = base_slug[:available].rstrip("-")
            if not trimmed:
                trimmed = f"device-{device.pk}"[:available].rstrip("-")
            return f"{trimmed}{suffix}"

        counter = 1
        slug = build_slug(counter)
        while slug in existing_slugs:
            counter += 1
            slug = build_slug(counter)
        name = _("%(device)s Stream") % {"device": device.display_name}
        return MjpegStream.objects.create(
            name=name,
            slug=slug,
            video_device=device,
            is_active=True,
        )

    def _redirect_admin(self, url_name):
        return redirect(f"admin:{url_name}")

    def _ensure_video_feature_enabled(
        self,
        request,
        action_label: str,
        *,
        auto_enable: bool = False,
        require_stack: bool = True,
        silent: bool = False,
    ):
        try:
            feature = NodeFeature.objects.get(slug="video-cam")
        except NodeFeature.DoesNotExist:
            if not silent:
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
            if not require_stack or has_rpi_camera_stack():
                NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
                return feature

        if not silent:
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

    def _capture_snapshot_for_device(
        self,
        request,
        device: VideoDevice,
        *,
        auto_enable: bool = False,
        link_duplicates: bool = False,
        silent: bool = False,
    ) -> VideoSnapshot | None:
        feature = self._ensure_video_feature_enabled(
            request,
            _("Snapshot"),
            auto_enable=auto_enable,
            silent=silent,
        )
        if not feature:
            return None

        node = self._get_local_node(request)
        if node is None:
            return None
        if device.node_id != node.id:
            if not silent:
                self.message_user(
                    request,
                    _("Snapshots can only be captured for the local node."),
                    level=messages.WARNING,
                )
            return None

        try:
            snapshot = device.capture_snapshot(link_duplicates=link_duplicates)
        except Exception as exc:  # pragma: no cover - depends on camera stack
            if not silent:
                self.message_user(request, str(exc), level=messages.ERROR)
            return None

        if not snapshot:
            if not silent:
                self.message_user(
                    request,
                    _("Duplicate snapshot; not saved"),
                    level=messages.INFO,
                )
            return None

        NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
        if not silent:
            self.message_user(
                request,
                _("Snapshot saved to %(path)s") % {"path": snapshot.sample.path},
                level=messages.SUCCESS,
            )
        return snapshot

    def find_devices_view(self, request):
        feature = self._ensure_video_feature_enabled(
            request, _("Find"), auto_enable=True, require_stack=False
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

        discovery = start_discovery(
            _("Discover"),
            request,
            model=self.model,
            metadata={"action": "video_find_devices"},
        )
        created, updated, created_devices, updated_devices = (
            VideoDevice.refresh_from_system(node=node, return_objects=True)
        )
        if discovery:
            for device_list, is_created in [
                (created_devices, True),
                (updated_devices, False),
            ]:
                for device in device_list:
                    record_discovery_item(
                        discovery,
                        obj=device,
                        label=device.identifier,
                        created=is_created,
                        overwritten=not is_created,
                    )
            discovery.metadata = {
                "action": "video_find_devices",
                "created": created,
                "updated": updated,
            }
            discovery.save(update_fields=["metadata"])

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
        device = (
            VideoDevice.objects.filter(node=node)
            .order_by("-is_default", "pk")
            .first()
        )
        snapshot = self._capture_snapshot_for_device(
            request,
            device,
            auto_enable=True,
            link_duplicates=True,
        )
        if not snapshot:
            return redirect("..")
        try:
            change_url = reverse(
                "admin:content_contentsample_change",
                args=[snapshot.sample.pk],
            )
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
            "title": _("Video Camera Stream"),
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
    search_fields = ("name", "slug", "video_device__name", "video_device__slug")
    list_filter = ("is_active",)
    change_form_template = "admin/video/mjpegstream/change_form.html"
    actions = ("take_selected_snapshots",)

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

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}

        def _get_sample_url(sample_id):
            if not sample_id:
                return None
            try:
                return reverse("admin:content_contentsample_change", args=[sample_id])
            except NoReverseMatch:  # pragma: no cover - admin URL always registered
                return None

        stream = self.get_object(request, object_id)
        if stream:
            preview = stream.get_thumbnail_data_uri() or stream.get_last_frame_data_uri()
            extra_context["last_frame_preview"] = preview
            extra_context["last_frame_sample_url"] = _get_sample_url(
                stream.last_frame_sample_id
            )
            extra_context["last_thumbnail_sample_url"] = _get_sample_url(
                stream.last_thumbnail_sample_id
            )
        return super().change_view(
            request, object_id, form_url=form_url, extra_context=extra_context
        )

    @admin.action(description=_("Take selected snapshots"))
    def take_selected_snapshots(self, request, queryset):
        captured = 0
        skipped = 0
        failed = 0

        for stream in queryset:
            try:
                frame_bytes = stream.capture_frame_bytes()
            except (MjpegDependencyError, MjpegDeviceUnavailableError, RuntimeError):
                failed += 1
                continue
            except Exception:
                failed += 1
                continue

            if not frame_bytes:
                skipped += 1
                continue

            try:
                stream.store_frame_bytes(frame_bytes, update_thumbnail=True)
            except Exception:
                failed += 1
                continue
            captured += 1

        if captured:
            self.message_user(
                request,
                _("Captured snapshots for %(count)s stream(s).")
                % {"count": captured},
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                _("Skipped %(count)s stream(s) without frames.")
                % {"count": skipped},
                level=messages.WARNING,
            )
        if failed:
            self.message_user(
                request,
                _("Failed to capture snapshots for %(count)s stream(s).")
                % {"count": failed},
                level=messages.ERROR,
            )


@admin.register(YoutubeChannel)
class YoutubeChannelAdmin(EntityModelAdmin):
    list_display = ("title", "handle_display", "channel_id", "channel_url")
    search_fields = ("title", "channel_id", "handle", "description")
    readonly_fields = ("channel_url",)
    actions = ("test_selected_channel",)

    @admin.display(description=_("Handle"))
    def handle_display(self, obj):
        return obj.get_handle(include_at=True)

    @admin.display(description=_("Channel URL"))
    def channel_url(self, obj):
        return obj.get_channel_url()

    @admin.action(description=_("Test selected channel"))
    def test_selected_channel(self, request, queryset):
        if not queryset.exists():
            self.message_user(
                request,
                _("No channels were selected."),
                level=messages.WARNING,
            )
            return

        tested = 0
        failed = 0
        missing = 0

        for channel in queryset:
            url = channel.get_channel_url()
            if not url:
                missing += 1
                self.message_user(
                    request,
                    _("Channel %(channel)s is missing a handle or channel ID.")
                    % {"channel": channel},
                    level=messages.WARNING,
                )
                continue
            try:
                response = requests.get(url, timeout=5)
            except requests.RequestException as exc:
                failed += 1
                self.message_user(
                    request,
                    _("Failed to reach %(channel)s: %(error)s")
                    % {"channel": channel, "error": exc},
                    level=messages.ERROR,
                )
                continue
            if response.ok:
                tested += 1
                continue
            failed += 1
            self.message_user(
                request,
                _("Channel %(channel)s returned HTTP %(status)s.")
                % {"channel": channel, "status": response.status_code},
                level=messages.ERROR,
            )

        if tested:
            self.message_user(
                request,
                _("Tested %(count)s channel(s).") % {"count": tested},
                level=messages.SUCCESS,
            )
        if failed:
            self.message_user(
                request,
                _("Failed to test %(count)s channel(s).") % {"count": failed},
                level=messages.ERROR,
            )
        if missing:
            self.message_user(
                request,
                _("Skipped %(count)s channel(s) without identifiers.")
                % {"count": missing},
                level=messages.WARNING,
            )
