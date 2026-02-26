"""Tests for the legacy camera_service command wrapper."""

from unittest.mock import patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.integration


@patch("apps.video.management.commands.camera_service.call_command")
def test_camera_service_command_forwards_interval_and_sleep(call_command_mock):
    """Ensure camera_service forwards long-lived service options to ``video service``."""

    call_command("camera_service", interval=0.25, sleep=0.1)

    call_command_mock.assert_called_once_with("video", "service", interval=0.25, sleep=0.1)
