import itertools

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.nodes.models import Node
from apps.video.frame_cache import CachedFrame
from apps.video.models import MjpegStream, VideoDevice


@pytest.fixture
def video_device(db):
    """Create a local video device attached to the self node."""

    node = Node.objects.create(
        hostname="local", mac_address=Node.get_current_mac(), current_relation=Node.Relation.SELF
    )
    return VideoDevice.objects.create(
        node=node,
        identifier="/dev/video-test",
        description="Test camera",
    )


@pytest.mark.parametrize(
    ("route_name", "expected_status"),
    [("video:mjpeg-admin-stream", 200), ("video:mjpeg-admin-probe", 204)],
)
@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_admin_endpoints_allow_inactive_for_staff(
    client,
    django_user_model,
    video_device,
    monkeypatch,
    route_name,
    expected_status,
):
    """Allow staff to access admin stream/probe endpoints even when a stream is inactive."""

    stream = MjpegStream.objects.create(
        name="Inactive", slug="inactive", video_device=video_device, is_active=False
    )
    user = django_user_model.objects.create_user("staff", password="pass", is_staff=True)
    client.force_login(user)

    cached = CachedFrame(frame_bytes=b"fresh-frame", frame_id=2, captured_at=None)
    monkeypatch.setattr("apps.video.views.get_frame", lambda _stream: cached)

    if route_name == "video:mjpeg-admin-stream":

        def fake_stream(_stream, *, first_frame):
            yield b"--frame\\r\\n" + first_frame.frame_bytes + b"\\r\\n"

        monkeypatch.setattr("apps.video.views.mjpeg_frame_stream", fake_stream)
    else:

        def fake_store(self, frame_bytes, update_thumbnail=True):
            assert frame_bytes == b"fresh-frame"

        monkeypatch.setattr(MjpegStream, "store_frame_bytes", fake_store)

    response = client.get(reverse(route_name, args=[stream.slug]))
    assert response.status_code == expected_status
    if route_name == "video:mjpeg-admin-stream":
        assert response["Content-Type"].startswith("multipart/x-mixed-replace")
        list(itertools.islice(response.streaming_content, 1))
        response.close()
