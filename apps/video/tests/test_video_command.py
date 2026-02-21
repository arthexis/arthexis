"""Tests for the video management command."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.video.models import VideoDevice

pytestmark = pytest.mark.integration


@pytest.mark.django_db
def test_video_command_lists_devices(capsys):
    """List configured video devices."""

    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    VideoDevice.objects.create(
        node=node,
        identifier="/dev/video0",
        description="Test camera",
        is_default=True,
    )

    with patch("apps.video.management.commands.video.Node.get_local", return_value=node):
        call_command("video")

    output = capsys.readouterr().out
    assert "Video devices: 1" in output
    assert "/dev/video0" in output


@pytest.mark.django_db
def test_video_command_discover(capsys):
    """Discover video devices from the local node."""

    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    NodeFeature.objects.create(slug="video-cam", display="Video Camera")

    with (
        patch("apps.video.management.commands.video.Node.get_local", return_value=node),
        patch(
            "apps.video.management.commands.video.VideoDevice.refresh_from_system",
            return_value=(1, 0),
        ) as refresh_mock,
    ):
        call_command("video", discover=True)

    refresh_mock.assert_called_once_with(node=node)
    assert "Detected 1 new" in capsys.readouterr().out


@pytest.mark.django_db
def test_video_command_errors_without_node():
    """Raise an error when no local node is registered."""

    with patch("apps.video.management.commands.video.Node.get_local", return_value=None):
        with pytest.raises(CommandError):
            call_command("video", discover=True)


@pytest.mark.django_db
def test_video_command_sample_creates_video(capsys, tmp_path, monkeypatch):
    """Capture sample frames and assemble a video."""

    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    feature = NodeFeature.objects.create(slug="video-cam", display="Video Camera")
    NodeFeatureAssignment.objects.create(node=node, feature=feature)
    VideoDevice.objects.create(
        node=node,
        identifier="/dev/video0",
        description="Test camera",
        is_default=True,
    )
    snapshot_path = tmp_path / "shot.jpg"
    snapshot_path.write_text("frame")

    monkeypatch.setattr(
        "apps.video.management.commands.video.WORK_DIR", tmp_path, raising=False
    )
    monkeypatch.setattr(
        VideoDevice,
        "capture_snapshot_path",
        lambda self: snapshot_path,
        raising=False,
    )

    def fake_encode(self, frames_dir: Path, output_path: Path) -> None:
        output_path.write_text("video")

    monkeypatch.setattr(
        "apps.video.management.commands.video.Command._encode_video", fake_encode
    )

    with patch("apps.video.management.commands.video.Node.get_local", return_value=node):
        call_command("video", samples=1)

    output = capsys.readouterr().out
    assert "Sample video saved" in output


@override_settings(VIDEO_FRAME_REDIS_URL="redis://localhost:6379/0")
@patch("apps.video.management.commands.video.get_status")
@patch("apps.video.management.commands.video.get_frame")
@patch("apps.video.management.commands.video.get_frame_cache")
@patch("apps.video.management.commands.video.MjpegStream")
@patch("apps.video.management.commands.video.VideoDevice")
@patch("apps.video.management.commands.video.NodeFeatureAssignment")
@patch("apps.video.management.commands.video.NodeFeature")
@patch("apps.video.management.commands.video.Node")
def test_video_doctor_reports_frame_cache(
    node_mock,
    feature_mock,
    assignment_mock,
    device_mock,
    stream_mock,
    frame_cache_mock,
    frame_mock,
    status_mock,
    capsys,
):
    """Ensure the doctor flow reports Redis connectivity and cached frames."""

    node = SimpleNamespace(hostname="local", pk=1)
    node_mock.get_local.return_value = node

    feature = SimpleNamespace(is_enabled=True, pk=7)
    feature_mock.objects.filter.return_value.first.return_value = feature
    assignment_mock.objects.filter.return_value.exists.return_value = True

    device_queryset = device_mock.objects.all.return_value.filter.return_value
    device_queryset.count.return_value = 1
    device_mock.get_default_for_node.return_value = SimpleNamespace(
        pk=11, display_name="Lobby Cam", identifier="/dev/video0"
    )

    stream_mock.objects.aggregate.return_value = {"total": 2, "active": 1}
    stream_mock.objects.filter.return_value.order_by.return_value.first.return_value = (
        SimpleNamespace(slug="lobby-stream")
    )

    redis_client = Mock()
    frame_cache_mock.return_value = redis_client
    frame_mock.return_value = SimpleNamespace(captured_at=timezone.now())
    status_mock.return_value = {}

    call_command("video", "--doctor")

    output = capsys.readouterr().out
    assert "Video Doctor" in output
    assert "Frame cache: Redis reachable." in output
    assert "Latest cached frame" in output

@patch("apps.video.management.commands.video.VideoDevice")
@patch("apps.video.management.commands.video.NodeFeature")
@patch("apps.video.management.commands.video.Node")
def test_video_command_snapshot_auto_enables_feature(node_mock, feature_mock, device_mock):
    """Auto-enable the camera feature when snapshot capture is requested."""

    node = SimpleNamespace()
    node_mock.get_local.return_value = node
    feature = SimpleNamespace(is_enabled=False)
    feature_mock.objects.get.return_value = feature

    device = SimpleNamespace(capture_snapshot=Mock(return_value=None))
    queryset = device_mock.objects.all.return_value.filter.return_value
    queryset.exists.return_value = True
    queryset.order_by.return_value.first.return_value = device

    with patch("apps.video.management.commands.video.NodeFeatureAssignment") as assignment_mock:
        call_command("video", snapshot=True)

    assignment_mock.objects.update_or_create.assert_called_once_with(node=node, feature=feature)


@patch("apps.video.management.commands.video.get_frame")
@patch("apps.video.management.commands.video.MjpegStream")
@patch("apps.video.management.commands.video.Node")
@pytest.mark.django_db
def test_video_command_mjpeg_capture(node_mock, stream_mock, frame_mock, capsys):
    """Capture MJPEG cached frames through the unified command."""

    node_mock.get_local.return_value = None
    stream = SimpleNamespace(store_frame_bytes=Mock(), slug="stream-1")
    stream_mock.objects.all.return_value.filter.return_value.order_by.return_value = [stream]
    frame_mock.return_value = SimpleNamespace(frame_bytes=b"frame")

    call_command("video", mjpeg=True)

    stream.store_frame_bytes.assert_called_once_with(b"frame", update_thumbnail=True)
    assert "Captured frames for 1 stream(s)." in capsys.readouterr().out
