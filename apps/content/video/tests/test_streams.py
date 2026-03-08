import itertools

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.nodes.models import Node
from apps.content.video.frame_cache import CachedFrame
from apps.content.video.models import MjpegStream, VideoDevice


@pytest.fixture
def video_device(db):
    node = Node.objects.create(
        hostname="local", mac_address=Node.get_current_mac(), current_relation=Node.Relation.SELF
    )
    return VideoDevice.objects.create(
        node=node,
        identifier="/dev/video-test",
        description="Test camera",
    )


@pytest.mark.parametrize(
    ("is_staff", "expected_configure"),
    [(True, True), (False, False)],
)
@pytest.mark.django_db
def test_stream_detail_role_visibility(
    client,
    django_user_model,
    video_device,
    is_staff,
    expected_configure,
):
    """Render stream detail for staff and anonymous users with role-specific controls."""

    stream = MjpegStream.objects.create(name="Lobby", slug="lobby", video_device=video_device)
    if is_staff:
        user = django_user_model.objects.create_user("staff", password="pass", is_staff=True)
        client.force_login(user)

    response = client.get(stream.get_absolute_url())

    assert response.status_code == 200
    content = response.content.decode()
    assert stream.get_stream_ws_path() in content
    configure_url = reverse("admin:video_mjpegstream_change", args=[stream.pk])
    if expected_configure:
        assert configure_url in content
    else:
        assert "Configure" not in content


@pytest.mark.parametrize("route_name", ["video:mjpeg-stream", "video:mjpeg-probe"])
@pytest.mark.django_db
def test_mjpeg_endpoints_require_camera_service_when_redis_missing(client, video_device, route_name):
    """Return 503 from stream/probe routes when Redis frame caching is unavailable."""

    stream = MjpegStream.objects.create(name="Hall", slug="hall", video_device=video_device)
    response = client.get(reverse(route_name, args=[stream.slug]))
    assert response.status_code == 503


@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_stream_serves_cached_frames(client, video_device, monkeypatch):
    stream = MjpegStream.objects.create(name="Hall", slug="hall", video_device=video_device)

    cached = CachedFrame(frame_bytes=b"frame-one", frame_id=1, captured_at=None)

    def fake_stream(_stream, *, first_frame):
        yield b"--frame\\r\\n" + first_frame.frame_bytes + b"\\r\\n"
        yield b"--frame\\r\\nframe-two\\r\\n"

    monkeypatch.setattr("apps.content.video.views.get_frame", lambda _stream: cached)
    monkeypatch.setattr("apps.content.video.views.mjpeg_frame_stream", fake_stream)

    response = client.get(reverse("video:mjpeg-stream", args=[stream.slug]))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("multipart/x-mixed-replace")

    chunks = list(itertools.islice(response.streaming_content, 2))
    assert chunks
    assert b"frame-one" in chunks[0]
    assert b"frame-two" in chunks[1]
    response.close()


@pytest.mark.parametrize("route_name", ["video:mjpeg-stream", "video:mjpeg-probe"])
@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_endpoints_return_unavailable_when_cache_empty(
    client,
    video_device,
    monkeypatch,
    route_name,
):
    """Return 503 from stream/probe routes when neither frame nor status is cached."""

    stream = MjpegStream.objects.create(name="Dock", slug="dock", video_device=video_device)
    monkeypatch.setattr("apps.content.video.views.get_frame", lambda _stream: None)
    monkeypatch.setattr("apps.content.video.views.get_status", lambda _stream: None)

    response = client.get(reverse(route_name, args=[stream.slug]))
    assert response.status_code == 503


@pytest.mark.parametrize("route_name", ["video:mjpeg-stream", "video:mjpeg-probe"])
@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_endpoints_use_cached_frames(client, video_device, monkeypatch, route_name):
    """Use cached frames for stream/probe routes when Redis returns a recent frame."""

    stream = MjpegStream.objects.create(name="Probe", slug="probe", video_device=video_device)
    cached = CachedFrame(frame_bytes=b"fresh-frame", frame_id=2, captured_at=None)
    monkeypatch.setattr("apps.content.video.views.get_frame", lambda _stream: cached)

    if route_name == "video:mjpeg-stream":
        def fake_stream(_stream, *, first_frame):
            yield b"--frame\\r\\n" + first_frame.frame_bytes + b"\\r\\n"
            yield b"--frame\\r\\nframe-two\\r\\n"

        monkeypatch.setattr("apps.content.video.views.mjpeg_frame_stream", fake_stream)
        response = client.get(reverse(route_name, args=[stream.slug]))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("multipart/x-mixed-replace")
        chunks = list(itertools.islice(response.streaming_content, 2))
        assert b"fresh-frame" in chunks[0]
        response.close()
        return

    captured: dict[str, bytes | bool] = {}

    def fake_store(self, frame_bytes, update_thumbnail=True):
        captured["frame"] = frame_bytes
        captured["update_thumbnail"] = update_thumbnail

    monkeypatch.setattr(MjpegStream, "store_frame_bytes", fake_store)
    response = client.get(reverse(route_name, args=[stream.slug]))
    assert response.status_code == 204
    assert captured["frame"] == b"fresh-frame"
    assert captured["update_thumbnail"] is True


@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_probe_returns_error_on_store_failure(client, video_device, monkeypatch):
    stream = MjpegStream.objects.create(name="Probe", slug="probe", video_device=video_device)

    cached = CachedFrame(frame_bytes=b"fresh-frame", frame_id=2, captured_at=None)

    def fake_store(self, frame_bytes, update_thumbnail=True):
        raise RuntimeError("disk error")

    monkeypatch.setattr("apps.content.video.views.get_frame", lambda _stream: cached)
    monkeypatch.setattr(MjpegStream, "store_frame_bytes", fake_store)

    response = client.get(reverse("video:mjpeg-probe", args=[stream.slug]))

    assert response.status_code == 503


@pytest.mark.parametrize(("is_staff", "expected_status"), [(False, 302), (True, 200)])
@pytest.mark.django_db
def test_mjpeg_debug_role_access(
    client,
    django_user_model,
    video_device,
    is_staff,
    expected_status,
):
    """Enforce staff-only access to the MJPEG debug page while validating staff links."""

    stream = MjpegStream.objects.create(name="Ops", slug="ops", video_device=video_device)
    if is_staff:
        user = django_user_model.objects.create_user("staff", password="pass", is_staff=True)
        client.force_login(user)

    response = client.get(reverse("video:mjpeg-debug", args=[stream.slug]))

    assert response.status_code == expected_status
    if expected_status == 200:
        content = response.content.decode()
        assert reverse("video:mjpeg-admin-stream", args=[stream.slug]) in content
        assert reverse("video:mjpeg-debug-status", args=[stream.slug]) in content
        assert reverse("video:mjpeg-admin-probe", args=[stream.slug]) in content


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
    monkeypatch.setattr("apps.content.video.views.get_frame", lambda _stream: cached)

    if route_name == "video:mjpeg-admin-stream":
        def fake_stream(_stream, *, first_frame):
            yield b"--frame\\r\\n" + first_frame.frame_bytes + b"\\r\\n"

        monkeypatch.setattr("apps.content.video.views.mjpeg_frame_stream", fake_stream)
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


@pytest.mark.django_db
def test_camera_gallery_lists_streams(client, video_device):
    stream = MjpegStream.objects.create(name="Lobby", slug="lobby", video_device=video_device)

    response = client.get(reverse("video:camera-gallery"))

    assert response.status_code == 200
    content = response.content.decode()
    assert stream.name in content
