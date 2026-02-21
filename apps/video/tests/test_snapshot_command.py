from unittest.mock import patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.integration


@patch("apps.video.management.commands.snapshot.call_command")
def test_snapshot_command_delegates_to_video(call_command_mock):
    """Ensure the legacy snapshot command delegates to ``video``."""

    call_command_mock.return_value = "/tmp/snapshot.jpg"

    result = call_command("snapshot")

    call_command_mock.assert_called_once_with("video", snapshot=True, auto_enable=True)
    assert result == "/tmp/snapshot.jpg"
