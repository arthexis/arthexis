"""Tests for the video management command doctor flow."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone


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

    device_queryset = device_mock.objects.filter.return_value
    device_queryset.count.return_value = 1
    device_mock.get_default_for_node.return_value = SimpleNamespace(
        pk=11, display_name="Lobby Cam", identifier="/dev/video0"
    )

    stream_mock.objects.count.return_value = 2
    active_queryset = stream_mock.objects.filter.return_value
    active_queryset.count.return_value = 1
    active_queryset.order_by.return_value.first.return_value = SimpleNamespace(
        slug="lobby-stream"
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
