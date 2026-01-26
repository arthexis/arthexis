import itertools

import pytest
from django.urls import reverse

from apps.nodes.models import Node
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
    assert stream.get_stream_url() in content


@pytest.mark.django_db
def test_stream_detail_is_public(client, video_device):
    stream = MjpegStream.objects.create(name="Garden", slug="garden", video_device=video_device)

    response = client.get(stream.get_absolute_url())

    assert response.status_code == 200
    content = response.content.decode()
    assert stream.get_stream_url() in content
    assert "Configure" not in content


@pytest.mark.django_db
def test_mjpeg_stream_serves_frames(client, video_device, monkeypatch):
    stream = MjpegStream.objects.create(name="Hall", slug="hall", video_device=video_device)

    def fake_frames(self):
        yield b"frame-one"
        yield b"frame-two"

    monkeypatch.setattr(MjpegStream, "iter_frame_bytes", fake_frames)

    response = client.get(reverse("video:mjpeg-stream", args=[stream.slug]))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("multipart/x-mixed-replace")

    chunks = list(itertools.islice(response.streaming_content, 2))
    assert chunks
    assert b"frame-one" in chunks[0]
    assert b"frame-two" in chunks[1]
    response.close()


@pytest.mark.django_db
def test_mjpeg_stream_stores_final_frame(client, video_device, monkeypatch):
    stream = MjpegStream.objects.create(name="Atrium", slug="atrium", video_device=video_device)
    captured: dict[str, bytes | bool] = {}

    def fake_frames(self):
        yield b"first-frame"
        yield b"final-frame"

    def fake_store(self, frame_bytes, update_thumbnail=True):
        captured["frame"] = frame_bytes
        captured["update_thumbnail"] = update_thumbnail

    monkeypatch.setattr(MjpegStream, "iter_frame_bytes", fake_frames)
    monkeypatch.setattr(MjpegStream, "store_frame_bytes", fake_store)

    response = client.get(reverse("video:mjpeg-stream", args=[stream.slug]))

    list(itertools.islice(response.streaming_content, 2))
    response.close()

    assert captured["frame"] == b"final-frame"
    assert captured["update_thumbnail"] is True


@pytest.mark.django_db
def test_mjpeg_stream_returns_no_content_when_no_frames(client, video_device, monkeypatch):
    stream = MjpegStream.objects.create(name="Empty", slug="empty", video_device=video_device)

    def empty_frames(self):
        yield from ()

    monkeypatch.setattr(MjpegStream, "iter_frame_bytes", empty_frames)

    response = client.get(reverse("video:mjpeg-stream", args=[stream.slug]))

    assert response.status_code == 204


@pytest.mark.django_db
def test_mjpeg_probe_captures_frame(client, video_device, monkeypatch):
    stream = MjpegStream.objects.create(name="Probe", slug="probe", video_device=video_device)
    captured: dict[str, bytes | bool] = {}

    def fake_capture(self):
        return b"fresh-frame"

    def fake_store(self, frame_bytes, update_thumbnail=True):
        captured["frame"] = frame_bytes
        captured["update_thumbnail"] = update_thumbnail

    monkeypatch.setattr(MjpegStream, "capture_frame_bytes", fake_capture)
    monkeypatch.setattr(MjpegStream, "store_frame_bytes", fake_store)

    response = client.get(reverse("video:mjpeg-probe", args=[stream.slug]))

    assert response.status_code == 204
    assert captured["frame"] == b"fresh-frame"
    assert captured["update_thumbnail"] is True


@pytest.mark.django_db
def test_camera_gallery_lists_streams(client, video_device):
    stream = MjpegStream.objects.create(name="Lobby", slug="lobby", video_device=video_device)

    response = client.get(reverse("video:camera-gallery"))

    assert response.status_code == 200
    content = response.content.decode()
    assert stream.name in content
