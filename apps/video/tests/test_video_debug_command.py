"""Tests for the legacy video_debug command wrapper."""

from unittest.mock import patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.integration


@patch("apps.video.management.commands.video_debug.call_command")
def test_video_debug_delegates_to_video(call_command_mock):
    """Ensure video_debug delegates options to the video command."""

    call_command("video_debug", snapshot=True, mjpeg=True, stream="main")

    call_command_mock.assert_called_once_with(
        "video",
        snapshot=True,
        device=None,
        discover=False,
        list_streams=False,
        auto_enable=False,
        mjpeg=True,
        stream="main",
        include_inactive=False,
    )


@patch("apps.video.management.commands.video_debug.call_command")
def test_video_debug_list_defaults_to_stream_listing(call_command_mock):
    """Ensure default video_debug execution includes legacy stream diagnostics."""

    call_command("video_debug")

    call_command_mock.assert_called_once_with(
        "video",
        snapshot=False,
        device=None,
        discover=False,
        list_streams=True,
        auto_enable=False,
        mjpeg=False,
        stream=None,
        include_inactive=False,
    )
