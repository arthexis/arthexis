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
def test_video_list_subcommand_discovers_and_lists_devices(capsys):
    """Prefer the list subcommand while keeping discovery behavior intact."""

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
        call_command("video", "list", "--discover")

    refresh_mock.assert_called_once_with(node=node)
    output = capsys.readouterr().out
    assert "Detected 1 new" in output
    assert "Video devices:" in output


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
        call_command("video", snapshot=True, auto_enable=True)

    assignment_mock.objects.update_or_create.assert_called_once_with(node=node, feature=feature)


@pytest.mark.parametrize(
    ("stream_arg", "frame_value", "expected_output", "expect_store_call"),
    [
        (None, SimpleNamespace(frame_bytes=b"frame"), "Captured frames for 1 stream(s).", True),
        ("123", None, "Skipped 1 stream(s) without frames.", False),
    ],
)
@patch("apps.video.management.commands.video.MjpegStream")
@patch("apps.video.management.commands.video.VideoDevice")
@patch("apps.video.management.commands.video.Node")
@pytest.mark.django_db
def test_video_command_mjpeg_capture_variants(
    node_mock,
    device_mock,
    stream_mock,
    stream_arg,
    frame_value,
    expected_output,
    expect_store_call,
    capsys,
):
    """Capture MJPEG frames while covering default and numeric-slug fallback selection."""

    node_mock.get_local.return_value = None
    stream = SimpleNamespace(store_frame_bytes=Mock(), slug="stream-1")
    queryset = stream_mock.objects.all.return_value.filter.return_value

    if stream_arg is None:
        queryset.order_by.return_value = [stream]
    else:
        device_mock.objects.all.return_value.filter.return_value.count.return_value = 0
        device_mock.objects.all.return_value.filter.return_value.order_by.return_value = []
        queryset.order_by.return_value = []
        queryset.filter.return_value.first.side_effect = [None, SimpleNamespace(slug="123")]

    with patch("apps.video.management.commands.video.get_frame", return_value=frame_value):
        kwargs = {"mjpeg": True}
        if stream_arg is not None:
            kwargs["stream"] = stream_arg
        call_command("video", **kwargs)

    if expect_store_call:
        stream.store_frame_bytes.assert_called_once_with(b"frame", update_thumbnail=True)
    else:
        stream.store_frame_bytes.assert_not_called()
    assert expected_output in capsys.readouterr().out


def _create_camera_service_sample_context(tmp_path, stream_slug):
    """Create a local node with one active camera stream for sample command tests."""

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
    device.mjpeg_streams.create(name="Lobby", slug=stream_slug, is_active=True)

    snapshot_path = tmp_path / "shot.jpg"
    snapshot_path.write_text("frame")
    return node, snapshot_path


@pytest.mark.parametrize(
    ("scenario", "samples", "expect_direct_calls", "expected_error", "expected_output"),
    [
        ("fresh_service", 2, 0, None, "Captured 2 sample frame(s) from camera service stream"),
        ("inactive_service", 1, 1, None, None),
        ("stale_status", 1, 1, None, None),
        ("missing_frame_id", 2, 0, None, None),
        ("timeout", 2, 0, "Timed out waiting for a new cached frame", None),
    ],
)
@pytest.mark.django_db
def test_video_command_sample_camera_service_scenarios(
    scenario,
    samples,
    expect_direct_calls,
    expected_error,
    expected_output,
    capsys,
    tmp_path,
    monkeypatch,
):
    """Exercise camera-service sampling scenarios with one shared setup path."""

    node, snapshot_path = _create_camera_service_sample_context(tmp_path, f"lobby-{scenario}")
    monkeypatch.setattr("apps.video.management.commands.video.WORK_DIR", tmp_path, raising=False)
    monkeypatch.setattr(
        "apps.video.management.commands.video.frame_cache_url",
        lambda: "redis://localhost:6379/0",
    )

    direct_calls = {"count": 0}

    def direct_capture(self):
        direct_calls["count"] += 1
        return snapshot_path

    def fail_direct_capture(self):
        raise AssertionError("direct camera capture should not be used")

    def fake_encode(self, frames_dir: Path, output_path: Path) -> None:
        output_path.write_text("video")

    monkeypatch.setattr("apps.video.management.commands.video.Command._encode_video", fake_encode)

    if scenario in {"inactive_service", "stale_status"}:
        monkeypatch.setattr(VideoDevice, "capture_snapshot_path", direct_capture, raising=False)
    else:
        monkeypatch.setattr(VideoDevice, "capture_snapshot_path", fail_direct_capture, raising=False)

    if scenario == "inactive_service":
        monkeypatch.setattr("apps.video.management.commands.video.get_status", lambda _stream: None)
        monkeypatch.setattr("apps.video.management.commands.video.get_frame", lambda _stream: None)
    elif scenario == "stale_status":
        monkeypatch.setattr(
            "apps.video.management.commands.video.get_status",
            lambda _stream: {"updated_at": "2000-01-01T00:00:00+00:00"},
        )
        monkeypatch.setattr("apps.video.management.commands.video.get_frame", lambda _stream: None)
    elif scenario == "timeout":
        monkeypatch.setattr(
            "apps.video.management.commands.video.get_status",
            lambda _stream: {"updated_at": "2026-01-01T00:00:00+00:00"},
        )
        monkeypatch.setattr(
            "apps.video.management.commands.video.get_frame",
            lambda _stream: SimpleNamespace(frame_bytes=b"frame-1", frame_id=1),
        )
        monkeypatch.setattr(
            "apps.video.management.commands.video.Command._CAMERA_SERVICE_FRAME_TIMEOUT_SECONDS",
            0.01,
        )
        monkeypatch.setattr(
            "apps.video.management.commands.video.Command._CAMERA_SERVICE_FRAME_POLL_SECONDS",
            0.001,
        )
    elif scenario == "missing_frame_id":
        monkeypatch.setattr(
            "apps.video.management.commands.video.get_status",
            lambda _stream: {"updated_at": "2026-01-01T00:00:00+00:00"},
        )
        frame_responses = iter(
            [
                SimpleNamespace(frame_bytes=b"frame-1", frame_id=None),
                SimpleNamespace(frame_bytes=b"frame-1", frame_id=None),
                SimpleNamespace(frame_bytes=b"frame-2", frame_id=None),
            ]
        )

        monkeypatch.setattr(
            "apps.video.management.commands.video.get_frame",
            lambda _stream: next(
                frame_responses,
                SimpleNamespace(frame_bytes=b"frame-2", frame_id=None),
            ),
        )
    else:
        monkeypatch.setattr(
            "apps.video.management.commands.video.get_status",
            lambda _stream: {"updated_at": "2026-01-01T00:00:00+00:00"},
        )
        frame_responses = iter(
            [
                SimpleNamespace(frame_bytes=b"frame-1", frame_id=1),
                SimpleNamespace(frame_bytes=b"frame-1", frame_id=1),
                SimpleNamespace(frame_bytes=b"frame-2", frame_id=2),
            ]
        )
        monkeypatch.setattr(
            "apps.video.management.commands.video.get_frame",
            lambda _stream: next(
                frame_responses,
                SimpleNamespace(frame_bytes=b"frame-2", frame_id=2),
            ),
        )

    with patch("apps.video.management.commands.video.Node.get_local", return_value=node):
        if expected_error:
            with pytest.raises(CommandError, match=expected_error):
                call_command("video", samples=samples)
        else:
            call_command("video", samples=samples)

    assert direct_calls["count"] == expect_direct_calls
    output = capsys.readouterr().out
    if expected_output:
        assert expected_output in output


@pytest.mark.parametrize("mode", ["snapshot", "mjpeg", "doctor"])
@pytest.mark.django_db
def test_video_subaction_modes_match_legacy_flags(mode, capsys):
    """Ensure sub-action CLI forms preserve legacy flag behavior for major video actions."""

    if mode == "snapshot":
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

        class SnapshotResult:
            """Simple snapshot result shim for command output checks."""

            sample = SimpleNamespace(path="/tmp/snapshot.jpg")

        with (
            patch("apps.video.management.commands.video.Node.get_local", return_value=node),
            patch.object(VideoDevice, "capture_snapshot", return_value=SnapshotResult(), autospec=True),
        ):
            call_command("video", snapshot=True)
        legacy_output = capsys.readouterr().out
        with (
            patch("apps.video.management.commands.video.Node.get_local", return_value=node),
            patch.object(VideoDevice, "capture_snapshot", return_value=SnapshotResult(), autospec=True),
        ):
            call_command("video", "snapshot")
        action_output = capsys.readouterr().out
        assert "Snapshot saved to /tmp/snapshot.jpg" in legacy_output
        assert "Snapshot saved to /tmp/snapshot.jpg" in action_output
        return

    if mode == "mjpeg":
        stream = SimpleNamespace(store_frame_bytes=Mock(), slug="stream-1")
        with (
            patch("apps.video.management.commands.video.Node.get_local", return_value=None),
            patch("apps.video.management.commands.video.MjpegStream") as stream_mock,
            patch("apps.video.management.commands.video.get_frame", return_value=SimpleNamespace(frame_bytes=b"frame")),
        ):
            stream_mock.objects.all.return_value.filter.return_value.order_by.return_value = [stream]
            call_command("video", mjpeg=True)
            legacy_output = capsys.readouterr().out

            stream.store_frame_bytes.reset_mock()
            call_command("video", "mjpeg")
            action_output = capsys.readouterr().out

        assert "Captured frames for 1 stream(s)." in legacy_output
        assert "Captured frames for 1 stream(s)." in action_output
        return

    with patch("apps.video.management.commands.video.Command._run_doctor") as doctor_mock:
        call_command("video", doctor=True)
    doctor_mock.assert_called_once()

    with patch("apps.video.management.commands.video.Command._run_doctor") as doctor_mock:
        call_command("video", "doctor")
    doctor_mock.assert_called_once()
    assert capsys.readouterr().out == ""


def test_video_service_subaction_invokes_service_runner():
    """Ensure service sub-action dispatches to the long-running service loop."""

    with patch("apps.video.management.commands.video.Command._run_service") as service_mock:
        call_command("video", "service", interval=0.33, sleep=0.12)

    service_mock.assert_called_once_with(interval=0.33, sleep=0.12)


def test_camera_service_supported_alias_delegates_to_video_service(capsys):
    """Keep the short alias as a supported synonym for ``video service``."""

    with patch("apps.video.management.commands.camera_service.call_command") as call_mock:
        call_command("camera_service", interval=0.25, sleep=0.1)

    call_mock.assert_called_once_with("video", "service", interval=0.25, sleep=0.1)
    assert "supported alias" in capsys.readouterr().out


@override_settings(VIDEO_FRAME_CAPTURE_INTERVAL=0.0, VIDEO_FRAME_SERVICE_SLEEP=0.0)
def test_video_command_setting_defaults_preserve_zero():
    """Preserve explicit zero-valued interval/sleep settings as CLI defaults."""

    from apps.video.management.commands.video import _setting_default_float

    assert _setting_default_float("VIDEO_FRAME_CAPTURE_INTERVAL", 0.2) == 0.0
    assert _setting_default_float("VIDEO_FRAME_SERVICE_SLEEP", 0.05) == 0.0
