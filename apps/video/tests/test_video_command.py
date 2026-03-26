"""Tests for the video management command."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.video.models import VideoDevice

pytestmark = pytest.mark.integration


@pytest.mark.django_db
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


def test_video_service_subaction_invokes_service_runner():
    """Ensure service sub-action dispatches to the long-running service loop."""

    with patch("apps.video.management.commands.video.Command._run_service") as service_mock:
        call_command("video", "service", interval=0.33, sleep=0.12)

    service_mock.assert_called_once_with(interval=0.33, sleep=0.12)

@override_settings(VIDEO_FRAME_CAPTURE_INTERVAL=0.0, VIDEO_FRAME_SERVICE_SLEEP=0.0)
def test_video_command_setting_defaults_preserve_zero():
    """Preserve explicit zero-valued interval/sleep settings as CLI defaults."""

    from apps.video.management.commands.video import _setting_default_float

    assert _setting_default_float("VIDEO_FRAME_CAPTURE_INTERVAL", 0.2) == 0.0
    assert _setting_default_float("VIDEO_FRAME_SERVICE_SLEEP", 0.05) == 0.0
