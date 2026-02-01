from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.video.models import VideoDevice

pytestmark = pytest.mark.integration


@pytest.mark.django_db
def test_video_command_lists_devices(capsys):
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
    with patch("apps.video.management.commands.video.Node.get_local", return_value=None):
        with pytest.raises(CommandError):
            call_command("video", discover=True)


@pytest.mark.django_db
def test_video_command_sample_creates_video(capsys, tmp_path, monkeypatch):
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    feature = NodeFeature.objects.create(slug="video-cam", display="Video Camera")
    NodeFeatureAssignment.objects.create(node=node, feature=feature)
    device = VideoDevice.objects.create(
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
