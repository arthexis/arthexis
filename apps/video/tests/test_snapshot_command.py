"""Tests for the legacy snapshot command wrapper."""

from unittest.mock import patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.integration


@patch("apps.video.management.commands.snapshot.call_command")
def test_snapshot_command_delegates_to_video(call_command_mock):
    """Ensure the legacy snapshot command delegates to ``video snapshot``."""

    result = call_command("snapshot")

    call_command_mock.assert_called_once_with(
        "video", "snapshot", auto_enable=True, discover=True
    )
    assert result is None
