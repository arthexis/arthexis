import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.content.models import ContentSample
from apps.nodes.models import Node, NodeFeature
from apps.video import admin as video_admin
from apps.video.models import VideoDevice


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
    monkeypatch.setattr(
        video_admin, "capture_rpi_snapshot", lambda: snapshot_path
    )

    sample = ContentSample.objects.create(
        kind=ContentSample.IMAGE,
        path=str(snapshot_path),
        node=node,
    )
    captured_kwargs = {}

    def fake_save_screenshot(path, **kwargs):
        captured_kwargs.update(kwargs)
        return sample

    monkeypatch.setattr(video_admin, "save_screenshot", fake_save_screenshot)

    refreshed = {"called": False}

    def fake_refresh_from_system(cls, *, node):
        refreshed["called"] = True
        VideoDevice.objects.create(
            node=node,
            identifier="/dev/video0",
            description="Raspberry Pi Camera",
        )
        return (1, 0)

    monkeypatch.setattr(
        VideoDevice,
        "refresh_from_system",
        classmethod(fake_refresh_from_system),
    )

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

    def fail_capture():
        raise AssertionError("capture should not run without devices")

    monkeypatch.setattr(video_admin, "capture_rpi_snapshot", fail_capture)

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
