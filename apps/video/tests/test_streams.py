import itertools

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.nodes.models import Node
from apps.video.frame_cache import CachedFrame
from apps.video.models import MjpegStream, VideoDevice


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


@pytest.mark.django_db
def test_stream_detail_shows_configure_for_staff(client, django_user_model, video_device):
    stream = MjpegStream.objects.create(name="Lobby", slug="lobby", video_device=video_device)
    user = django_user_model.objects.create_user("staff", password="pass", is_staff=True)
    client.force_login(user)

    response = client.get(stream.get_absolute_url())

    assert response.status_code == 200
    content = response.content.decode()
    assert reverse("admin:video_mjpegstream_change", args=[stream.pk]) in content
    assert stream.get_stream_ws_path() in content


@pytest.mark.django_db
def test_stream_detail_is_public(client, video_device):
    stream = MjpegStream.objects.create(name="Garden", slug="garden", video_device=video_device)

    response = client.get(stream.get_absolute_url())

    assert response.status_code == 200
    content = response.content.decode()
    assert stream.get_stream_ws_path() in content
    assert "Configure" not in content


@pytest.mark.django_db
def test_mjpeg_stream_requires_camera_service_when_redis_missing(client, video_device):
    stream = MjpegStream.objects.create(name="Hall", slug="hall", video_device=video_device)

    response = client.get(reverse("video:mjpeg-stream", args=[stream.slug]))

    assert response.status_code == 503


@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_stream_serves_cached_frames(client, video_device, monkeypatch):
    stream = MjpegStream.objects.create(name="Hall", slug="hall", video_device=video_device)

    cached = CachedFrame(frame_bytes=b"frame-one", frame_id=1, captured_at=None)

    def fake_stream(_stream, *, first_frame):
        yield b"--frame\\r\\n" + first_frame.frame_bytes + b"\\r\\n"
        yield b"--frame\\r\\nframe-two\\r\\n"

    monkeypatch.setattr("apps.video.views.get_frame", lambda _stream: cached)
    monkeypatch.setattr("apps.video.views.mjpeg_frame_stream", fake_stream)

    response = client.get(reverse("video:mjpeg-stream", args=[stream.slug]))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("multipart/x-mixed-replace")

    chunks = list(itertools.islice(response.streaming_content, 2))
    assert chunks
    assert b"frame-one" in chunks[0]
    assert b"frame-two" in chunks[1]
    response.close()


@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_stream_returns_unavailable_when_cache_empty(
    client, video_device, monkeypatch
):
    stream = MjpegStream.objects.create(name="Dock", slug="dock", video_device=video_device)

    monkeypatch.setattr("apps.video.views.get_frame", lambda _stream: None)
    monkeypatch.setattr("apps.video.views.get_status", lambda _stream: None)

    response = client.get(reverse("video:mjpeg-stream", args=[stream.slug]))

    assert response.status_code == 503


@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_probe_uses_cached_frame(client, video_device, monkeypatch):
    stream = MjpegStream.objects.create(name="Probe", slug="probe", video_device=video_device)
    captured: dict[str, bytes | bool] = {}

    cached = CachedFrame(frame_bytes=b"fresh-frame", frame_id=2, captured_at=None)

    def fake_store(self, frame_bytes, update_thumbnail=True):
        captured["frame"] = frame_bytes
        captured["update_thumbnail"] = update_thumbnail

    monkeypatch.setattr("apps.video.views.get_frame", lambda _stream: cached)
    monkeypatch.setattr(MjpegStream, "store_frame_bytes", fake_store)

    response = client.get(reverse("video:mjpeg-probe", args=[stream.slug]))

    assert response.status_code == 204
    assert captured["frame"] == b"fresh-frame"
    assert captured["update_thumbnail"] is True


@pytest.mark.django_db
def test_mjpeg_probe_requires_camera_service_when_redis_missing(client, video_device):
    stream = MjpegStream.objects.create(name="Probe", slug="probe", video_device=video_device)

    response = client.get(reverse("video:mjpeg-probe", args=[stream.slug]))

    assert response.status_code == 503


@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_probe_returns_unavailable_when_cache_empty(
    client, video_device, monkeypatch
):
    stream = MjpegStream.objects.create(name="Probe", slug="probe", video_device=video_device)

    monkeypatch.setattr("apps.video.views.get_frame", lambda _stream: None)
    monkeypatch.setattr("apps.video.views.get_status", lambda _stream: None)

    response = client.get(reverse("video:mjpeg-probe", args=[stream.slug]))

    assert response.status_code == 503


@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_probe_returns_error_on_store_failure(client, video_device, monkeypatch):
    stream = MjpegStream.objects.create(name="Probe", slug="probe", video_device=video_device)

    cached = CachedFrame(frame_bytes=b"fresh-frame", frame_id=2, captured_at=None)

    def fake_store(self, frame_bytes, update_thumbnail=True):
        raise RuntimeError("disk error")

    monkeypatch.setattr("apps.video.views.get_frame", lambda _stream: cached)
    monkeypatch.setattr(MjpegStream, "store_frame_bytes", fake_store)

    response = client.get(reverse("video:mjpeg-probe", args=[stream.slug]))

    assert response.status_code == 503


@pytest.mark.django_db
def test_mjpeg_debug_requires_staff(client, video_device):
    stream = MjpegStream.objects.create(name="Lab", slug="lab", video_device=video_device)

    response = client.get(reverse("video:mjpeg-debug", args=[stream.slug]))

    assert response.status_code == 302


@pytest.mark.django_db
def test_mjpeg_debug_renders_for_staff(client, django_user_model, video_device):
    stream = MjpegStream.objects.create(name="Ops", slug="ops", video_device=video_device)
    user = django_user_model.objects.create_user("staff", password="pass", is_staff=True)
    client.force_login(user)

    response = client.get(reverse("video:mjpeg-debug", args=[stream.slug]))

    assert response.status_code == 200
    content = response.content.decode()
    assert reverse("video:mjpeg-admin-stream", args=[stream.slug]) in content
    assert reverse("video:mjpeg-debug-status", args=[stream.slug]) in content
    assert reverse("video:mjpeg-admin-probe", args=[stream.slug]) in content


@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_admin_stream_allows_inactive_for_staff(
    client, django_user_model, video_device, monkeypatch
):
    stream = MjpegStream.objects.create(
        name="Inactive", slug="inactive", video_device=video_device, is_active=False
    )
    user = django_user_model.objects.create_user("staff", password="pass", is_staff=True)
    client.force_login(user)

    cached = CachedFrame(frame_bytes=b"frame-one", frame_id=1, captured_at=None)

    def fake_stream(_stream, *, first_frame):
        yield b"--frame\\r\\n" + first_frame.frame_bytes + b"\\r\\n"

    monkeypatch.setattr("apps.video.views.get_frame", lambda _stream: cached)
    monkeypatch.setattr("apps.video.views.mjpeg_frame_stream", fake_stream)

    response = client.get(reverse("video:mjpeg-admin-stream", args=[stream.slug]))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("multipart/x-mixed-replace")
    list(itertools.islice(response.streaming_content, 1))
    response.close()


@pytest.mark.django_db
@override_settings(VIDEO_FRAME_REDIS_URL="redis://example.test/0")
def test_mjpeg_admin_probe_allows_inactive_for_staff(
    client, django_user_model, video_device, monkeypatch
):
    stream = MjpegStream.objects.create(
        name="Inactive", slug="inactive", video_device=video_device, is_active=False
    )
    user = django_user_model.objects.create_user("staff", password="pass", is_staff=True)
    client.force_login(user)

    cached = CachedFrame(frame_bytes=b"fresh-frame", frame_id=2, captured_at=None)

    def fake_store(self, frame_bytes, update_thumbnail=True):
        assert frame_bytes == b"fresh-frame"

    monkeypatch.setattr("apps.video.views.get_frame", lambda _stream: cached)
    monkeypatch.setattr(MjpegStream, "store_frame_bytes", fake_store)

    response = client.get(reverse("video:mjpeg-admin-probe", args=[stream.slug]))

    assert response.status_code == 204


@pytest.mark.django_db
def test_camera_gallery_lists_streams(client, video_device):
    stream = MjpegStream.objects.create(name="Lobby", slug="lobby", video_device=video_device)

    response = client.get(reverse("video:camera-gallery"))

    assert response.status_code == 200
    content = response.content.decode()
    assert stream.name in content
