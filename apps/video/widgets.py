"""Admin dashboard widgets for camera features."""

from __future__ import annotations

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.nodes.models import Node
from apps.widgets import register_widget
from apps.widgets.models import WidgetZone

from .models import MjpegStream, VideoDevice


@register_widget(
    slug="camera-sidebar",
    name=_("Camera"),
    zone=WidgetZone.ZONE_SIDEBAR,
    template_name="widgets/camera_sidebar.html",
    description=_("Default camera thumbnail and stream shortcut."),
    order=20,
    required_feature_slug="video-cam",
)
def camera_sidebar_widget(*, request, **_kwargs):
    """Render the camera sidebar widget when the camera feature is enabled."""

    node = getattr(request, "badge_node", None) or getattr(request, "node", None)
    if node is None:
        node = Node.get_local()
    if node is None:
        return None

    device = VideoDevice.get_default_for_node(node)
    if not device:
        return {"device": None, "stream": None, "thumbnail": None, "stream_url": None}

    stream = (
        MjpegStream.objects.filter(video_device=device, is_active=True)
        .order_by("name", "pk")
        .first()
    )
    thumbnail = stream.get_thumbnail_data_uri() if stream else None
    if not thumbnail and stream:
        thumbnail = stream.get_last_frame_data_uri()

    return {
        "device": device,
        "stream": stream,
        "thumbnail": thumbnail,
        "stream_url": reverse("video:stream-detail", args=[stream.slug]) if stream else None,
    }
