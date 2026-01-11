from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command

pytestmark = pytest.mark.integration


def _mock_feature(is_enabled: bool = True):
    return SimpleNamespace(is_enabled=is_enabled)


@patch("apps.video.management.commands.snapshot._is_test_server_active", return_value=True)
@patch("apps.video.management.commands.snapshot.save_screenshot")
@patch("apps.video.management.commands.snapshot.capture_rpi_snapshot")
@patch("apps.video.management.commands.snapshot.VideoDevice")
@patch("apps.video.management.commands.snapshot.NodeFeatureAssignment")
@patch("apps.video.management.commands.snapshot.NodeFeature")
@patch("apps.video.management.commands.snapshot.Node")
def test_snapshot_command_success(
    node_mock,
    feature_model_mock,
    assignment_mock,
    video_device_mock,
    capture_mock,
    save_mock,
    test_server_mock,
    capsys,
):
    node_instance = object()
    node_mock.get_local.return_value = node_instance
    feature_model_mock.objects.get.return_value = _mock_feature()
    video_device_mock.objects.filter.return_value.exists.return_value = True
    capture_mock.return_value = Path("/tmp/snapshot.jpg")
    save_mock.return_value = SimpleNamespace(path="/tmp/snapshot.jpg")

    result = call_command("snapshot")

    video_device_mock.objects.filter.assert_called_with(node=node_instance)
    assignment_mock.objects.update_or_create.assert_not_called()
    capture_mock.assert_called_once_with()
    save_mock.assert_called_once_with(
        Path("/tmp/snapshot.jpg"), node=node_instance, method="RPI_CAMERA", link_duplicates=True
    )
    assert "Snapshot saved" in capsys.readouterr().out
    assert result == "/tmp/snapshot.jpg"


@patch("apps.video.management.commands.snapshot._is_test_server_active", return_value=True)
@patch("apps.video.management.commands.snapshot.has_rpi_camera_stack", return_value=False)
@patch("apps.video.management.commands.snapshot.save_screenshot")
@patch("apps.video.management.commands.snapshot.capture_rpi_snapshot")
@patch("apps.video.management.commands.snapshot.VideoDevice")
@patch("apps.video.management.commands.snapshot.NodeFeatureAssignment")
@patch("apps.video.management.commands.snapshot.NodeFeature")
@patch("apps.video.management.commands.snapshot.Node")
def test_snapshot_command_enables_feature_and_refreshes_devices(
    node_mock,
    feature_model_mock,
    assignment_mock,
    video_device_mock,
    capture_mock,
    save_mock,
    stack_mock,
    test_server_mock,
    capsys,
):
    node_instance = object()
    node_mock.get_local.return_value = node_instance
    feature_model_mock.objects.get.return_value = _mock_feature(is_enabled=False)
    filter_mock = video_device_mock.objects.filter.return_value
    filter_mock.exists.side_effect = [False, True]
    video_device_mock.refresh_from_system.return_value = (1, 0)
    capture_mock.return_value = Path("/tmp/snapshot2.jpg")
    save_mock.return_value = SimpleNamespace(path="/tmp/snapshot2.jpg")

    result = call_command("snapshot")

    assignment_mock.objects.update_or_create.assert_called_once_with(
        node=node_instance, feature=feature_model_mock.objects.get.return_value
    )
    video_device_mock.refresh_from_system.assert_called_once_with(node=node_instance)
    capture_mock.assert_called_once_with()
    assert "Enabled the rpi-camera feature" in capsys.readouterr().out
    assert result == "/tmp/snapshot2.jpg"


@patch("apps.video.management.commands.snapshot._is_test_server_active", return_value=True)
@patch("apps.video.management.commands.snapshot.Node")
def test_snapshot_command_errors_without_node(node_mock, test_server_mock):
    node_mock.get_local.return_value = None
    with pytest.raises(CommandError):
        call_command("snapshot")


@patch("apps.video.management.commands.snapshot._is_test_server_active", return_value=True)
@patch("apps.video.management.commands.snapshot.Node")
@patch("apps.video.management.commands.snapshot.NodeFeature")
def test_snapshot_command_errors_without_feature(
    node_feature_mock, node_mock, test_server_mock
):
    node_mock.get_local.return_value = object()
    node_feature_mock.DoesNotExist = Exception
    node_feature_mock.objects.get.side_effect = node_feature_mock.DoesNotExist()

    with pytest.raises(CommandError):
        call_command("snapshot")


@patch("apps.video.management.commands.snapshot._is_test_server_active", return_value=True)
@patch("apps.video.management.commands.snapshot.Node")
@patch("apps.video.management.commands.snapshot.NodeFeature")
@patch("apps.video.management.commands.snapshot.VideoDevice")
@patch("apps.video.management.commands.snapshot.has_rpi_camera_stack", return_value=True)
@patch("apps.video.management.commands.snapshot.capture_rpi_snapshot")
@patch("apps.video.management.commands.snapshot.save_screenshot")
def test_snapshot_command_errors_without_devices(
    save_mock,
    capture_mock,
    stack_mock,
    video_device_mock,
    node_feature_mock,
    node_mock,
    test_server_mock,
):
    node_instance = object()
    node_mock.get_local.return_value = node_instance
    node_feature_mock.objects.get.return_value = _mock_feature()
    video_device_mock.objects.filter.return_value.exists.return_value = False
    video_device_mock.refresh_from_system.return_value = (0, 0)

    with pytest.raises(CommandError):
        call_command("snapshot")

    capture_mock.assert_not_called()
    save_mock.assert_not_called()


@patch("apps.video.management.commands.snapshot._is_test_server_active", return_value=False)
def test_snapshot_command_requires_test_server(test_server_mock):
    with pytest.raises(CommandError):
        call_command("snapshot")
