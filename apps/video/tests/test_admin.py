import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.content.models import ContentSample
from apps.nodes.models import Node, NodeFeature
from apps.video import admin as video_admin
from apps.video.models import VideoDevice, VideoSnapshot


@pytest.mark.django_db
def test_power_camera_action_links_to_docs_when_unconfigured(
    admin_client, settings, monkeypatch
):
    Node._local_cache.clear()
    Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    settings.USB_CAMERA_POWER_BUS = ""
    settings.USB_CAMERA_POWER_PORT = ""
    monkeypatch.setattr(video_admin.shutil, "which", lambda _: None)

    response = admin_client.get(
        reverse("admin:video_videodevice_power_off_camera"),
        follow=True,
    )

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("USB Camera Power-Off guide" in msg for msg in messages)
    assert response.status_code == 200
    assert response.request["PATH_INFO"].endswith(
        reverse("admin:video_videodevice_changelist")
    )


@pytest.mark.django_db
def test_power_camera_action_runs_uhubctl(
    admin_client, settings, monkeypatch
):
    Node._local_cache.clear()
    Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    settings.USB_CAMERA_POWER_BUS = "1"
    settings.USB_CAMERA_POWER_PORT = "7"
    monkeypatch.setattr(video_admin.shutil, "which", lambda _: "/usr/bin/uhubctl")

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(video_admin.subprocess, "run", fake_run)

    response = admin_client.get(
        reverse("admin:video_videodevice_power_off_camera"),
        follow=True,
    )

    assert captured["args"] == [
        "/usr/bin/uhubctl",
        "-l",
        "1",
        "-p",
        "7",
        "-a",
        "off",
    ]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert response.status_code == 200
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("USB camera power turned off" in msg for msg in messages)


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
    NodeFeature.objects.create(slug="rpi-camera", display="Raspberry Pi Camera")
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
    NodeFeature.objects.create(slug="rpi-camera", display="Raspberry Pi Camera")
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
    NodeFeature.objects.create(slug="rpi-camera", display="Raspberry Pi Camera")

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
    NodeFeature.objects.create(slug="rpi-camera", display="Raspberry Pi Camera")

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
