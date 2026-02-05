import pytest
import requests
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.utils.text import slugify
from django.urls import reverse

from apps.content.models import ContentSample
from apps.nodes.models import Node, NodeFeature
from apps.video import admin as video_admin
from apps.video.frame_cache import CachedFrame
from apps.video.models import MjpegStream, VideoDevice, VideoSnapshot, YoutubeChannel


@pytest.fixture
def admin_user(db):
    User = get_user_model()
    return User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password",
    )


@pytest.fixture
def video_device(db):
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    return VideoDevice.objects.create(
        node=node,
        identifier="/dev/video0",
        description="Raspberry Pi Camera",
    )


@pytest.mark.django_db
def test_take_snapshot_discovers_device_and_redirects(
    admin_client, monkeypatch, tmp_path
):
    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    NodeFeature.objects.create(slug="video-cam", display="Video Camera")
    monkeypatch.setattr(video_admin, "has_rpi_camera_stack", lambda: True)

    snapshot_path = tmp_path / "snapshot.jpg"
    snapshot_path.write_bytes(b"snapshot")

    sample = ContentSample.objects.create(
        kind=ContentSample.IMAGE,
        path=str(snapshot_path),
        node=node,
    )
    captured_kwargs = {}

    refreshed = {"called": False}

    def fake_refresh_from_system(cls, *, node):
        refreshed["called"] = True
        device = VideoDevice.objects.create(
            node=node,
            identifier="/dev/video0",
            description="Raspberry Pi Camera",
            is_default=True,
            capture_width=1280,
            capture_height=720,
        )
        return (1, 0)

    monkeypatch.setattr(
        VideoDevice,
        "refresh_from_system",
        classmethod(fake_refresh_from_system),
    )
    def fake_capture_snapshot(self, *, link_duplicates=False):
        captured_kwargs["link_duplicates"] = link_duplicates
        return VideoSnapshot.objects.create(
            device=self,
            sample=sample,
            width=1280,
            height=720,
            image_format="JPEG",
        )

    monkeypatch.setattr(VideoDevice, "capture_snapshot", fake_capture_snapshot)

    response = admin_client.get(
        reverse("admin:video_videodevice_take_snapshot")
    )

    assert refreshed["called"] is True
    assert captured_kwargs["link_duplicates"] is True
    assert response.status_code == 302
    assert response.url == reverse(
        "admin:content_contentsample_change", args=[sample.pk]
    )


@pytest.mark.django_db
def test_take_snapshot_warns_when_no_devices(
    admin_client, monkeypatch
):
    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    NodeFeature.objects.create(slug="video-cam", display="Video Camera")
    monkeypatch.setattr(video_admin, "has_rpi_camera_stack", lambda: True)

    def fake_refresh_from_system(cls, *, node):
        return (0, 0)

    monkeypatch.setattr(
        VideoDevice,
        "refresh_from_system",
        classmethod(fake_refresh_from_system),
    )

    def fail_capture(self, *, link_duplicates=False):
        raise AssertionError("capture should not run without devices")

    monkeypatch.setattr(VideoDevice, "capture_snapshot", fail_capture)

    response = admin_client.get(
        reverse("admin:video_videodevice_take_snapshot"),
        follow=True,
    )

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("No video devices were detected on this node." in msg for msg in messages)
    assert response.status_code == 200
    assert response.request["PATH_INFO"].endswith(
        reverse("admin:video_videodevice_changelist")
    )


@pytest.mark.django_db
def test_change_view_shows_latest_snapshot(admin_client, monkeypatch, tmp_path):
    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    NodeFeature.objects.create(slug="video-cam", display="Video Camera")

    image_path = tmp_path / "snapshot.jpg"

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is an installed dependency
        pytest.skip("Pillow not available")

    Image.new("RGB", (8, 6), color="red").save(image_path, format="JPEG")

    device = VideoDevice.objects.create(
        node=node,
        identifier="/dev/video0",
        description="Raspberry Pi Camera",
    )

    sample = ContentSample.objects.create(
        kind=ContentSample.IMAGE,
        path=str(image_path),
        node=node,
    )
    snapshot = VideoSnapshot.objects.create(
        device=device,
        sample=sample,
        **VideoSnapshot.build_metadata(sample),
    )

    url = reverse("admin:video_videodevice_change", args=[device.pk])
    response = admin_client.get(url)

    assert response.status_code == 200
    latest_snapshot = device.get_latest_snapshot()
    assert latest_snapshot is not None
    assert latest_snapshot.pk == snapshot.pk
    assert latest_snapshot.resolution_display == "8 × 6"
    assert latest_snapshot.image_format.lower() == "jpeg"
    assert VideoSnapshot.objects.filter(device=device).count() == 1
    assert "LATEST" in response.rendered_content
    sample_url = reverse("admin:content_contentsample_change", args=[sample.pk])
    assert sample_url in response.rendered_content


@pytest.mark.django_db
def test_change_view_captures_missing_snapshot(admin_client, monkeypatch, tmp_path):
    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    NodeFeature.objects.create(slug="video-cam", display="Video Camera")

    device = VideoDevice.objects.create(
        node=node,
        identifier="/dev/video0",
        description="Raspberry Pi Camera",
    )

    image_path = tmp_path / "snapshot.jpg"
    image_path.write_bytes(b"snapshot")

    captured = {"called": False}

    def fake_capture(self, request, target_device, **kwargs):
        captured["called"] = True
        sample = ContentSample.objects.create(
            kind=ContentSample.IMAGE,
            path=str(image_path),
            node=node,
        )
        return VideoSnapshot.objects.create(
            device=target_device,
            sample=sample,
            captured_at=sample.created_at,
            width=1,
            height=1,
            image_format="JPEG",
        )

    monkeypatch.setattr(
        video_admin.VideoDeviceAdmin,
        "_capture_snapshot_for_device",
        fake_capture,
    )

    url = reverse("admin:video_videodevice_change", args=[device.pk])
    response = admin_client.get(url)

    assert response.status_code == 200
    assert captured["called"] is True
    assert VideoSnapshot.objects.filter(device=device).count() == 1
    assert "refresh_snapshot" in response.rendered_content


@pytest.mark.django_db
def test_mjpeg_stream_action_captures_snapshot(admin_client, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    device = VideoDevice.objects.create(
        node=node,
        identifier="/dev/video0",
        description="Raspberry Pi Camera",
    )
    stream = MjpegStream.objects.create(
        name="Stream",
        slug="stream",
        video_device=device,
        is_active=True,
    )

    captured = {"store": 0}

    def fake_get_frame(_stream):
        return CachedFrame(frame_bytes=b"frame", frame_id=1, captured_at=None)

    def fake_store(self, frame_bytes, *, update_thumbnail=True):
        assert frame_bytes == b"frame"
        assert update_thumbnail is True
        captured["store"] += 1

    monkeypatch.setattr("apps.video.admin.get_frame", fake_get_frame)
    monkeypatch.setattr(MjpegStream, "store_frame_bytes", fake_store)

    response = admin_client.post(
        reverse("admin:video_mjpegstream_changelist"),
        {"action": "take_selected_snapshots", "_selected_action": [stream.pk]},
        follow=True,
    )

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("Captured snapshots for 1 stream" in msg for msg in messages)
    assert response.status_code == 200
    assert captured["store"] == 1


@pytest.mark.django_db
def test_goto_stream_creates_default_stream(admin_user):
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    device = VideoDevice.objects.create(
        node=node,
        identifier="/dev/video0",
        description="Raspberry Pi Camera",
        slug="camera",
    )
    admin_view = video_admin.VideoDeviceAdmin(VideoDevice, admin.site)
    request = RequestFactory().get("/")
    request.user = admin_user
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))

    response = admin_view.goto_stream(request, device)

    stream = MjpegStream.objects.get(video_device=device)
    assert response.status_code == 302
    assert response.url == stream.get_admin_url()
    messages = [str(message) for message in request._messages]
    assert any("Created MJPEG stream" in msg for msg in messages)


@pytest.mark.django_db
def test_videodevice_view_on_site_uses_stream(video_device):
    stream = MjpegStream.objects.create(
        name="Stream",
        slug="stream",
        video_device=video_device,
        is_active=True,
    )
    admin_view = video_admin.VideoDeviceAdmin(VideoDevice, admin.site)

    assert admin_view.get_view_on_site_url(video_device) == stream.get_absolute_url()


@pytest.mark.django_db
def test_videodevice_view_on_site_falls_back_to_gallery(video_device):
    MjpegStream.objects.create(
        name="Inactive Stream",
        slug="inactive-stream",
        video_device=video_device,
        is_active=False,
    )
    admin_view = video_admin.VideoDeviceAdmin(VideoDevice, admin.site)

    assert admin_view.get_view_on_site_url(video_device) == reverse(
        "video:camera-gallery"
    )


@pytest.mark.django_db
def test_create_default_stream_truncates_and_uniques_slug(admin_user):
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    long_slug = "camera-" + ("x" * 80)
    device = VideoDevice.objects.create(
        node=node,
        identifier="/dev/video0",
        description="Raspberry Pi Camera",
        slug=long_slug,
    )
    admin_view = video_admin.VideoDeviceAdmin(VideoDevice, admin.site)
    slug_field = MjpegStream._meta.get_field("slug")
    max_length = slug_field.max_length or 50
    existing_slug = slugify(long_slug)[:max_length].rstrip("-")
    MjpegStream.objects.create(
        name="Existing",
        slug=existing_slug,
        video_device=device,
        is_active=True,
    )

    stream = admin_view._create_default_stream(device)

    assert len(stream.slug) <= max_length
    assert stream.slug != existing_slug
    assert stream.slug.startswith(existing_slug[: max_length - 2])


def build_admin_request(factory, user):
    request = factory.post("/admin/")
    request.user = user
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    messages_storage = FallbackStorage(request)
    setattr(request, "_messages", messages_storage)
    return request


@pytest.mark.django_db
def test_youtube_channel_action_reports_success(monkeypatch, admin_user):
    channel = YoutubeChannel.objects.create(
        title="Arthexis",
        channel_id="UC1234abcd",
    )
    request = build_admin_request(RequestFactory(), admin_user)
    admin_view = video_admin.YoutubeChannelAdmin(YoutubeChannel, admin.site)
    captured = {}

    def fake_get(url, timeout):
        captured["url"] = url

        class Response:
            ok = True
            status_code = 200

        return Response()

    monkeypatch.setattr(video_admin.requests, "get", fake_get)

    admin_view.test_selected_channel(
        request,
        YoutubeChannel.objects.filter(pk=channel.pk),
    )

    messages = [str(message) for message in request._messages]
    assert channel.get_channel_url() == captured["url"]
    assert any("Tested 1 channel" in message for message in messages)


@pytest.mark.django_db
def test_youtube_channel_action_reports_failure(monkeypatch, admin_user):
    channel = YoutubeChannel.objects.create(
        title="Arthexis",
        channel_id="UC9999fail",
    )
    request = build_admin_request(RequestFactory(), admin_user)
    admin_view = video_admin.YoutubeChannelAdmin(YoutubeChannel, admin.site)

    def fake_get(url, timeout):
        raise requests.RequestException("network down")

    monkeypatch.setattr(video_admin.requests, "get", fake_get)

    admin_view.test_selected_channel(
        request,
        YoutubeChannel.objects.filter(pk=channel.pk),
    )

    messages = [str(message) for message in request._messages]
    assert any("Failed to reach" in message for message in messages)
    assert any("Failed to test 1 channel" in message for message in messages)
