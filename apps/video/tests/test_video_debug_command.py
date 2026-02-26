"""Tests for the legacy video_debug command wrapper."""

from unittest.mock import call, patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.integration


@patch("apps.video.management.commands.video_debug.call_command")
def test_video_debug_delegates_actions_to_video(call_command_mock):
    """Ensure video_debug delegates list/snapshot/mjpeg actions to video command."""

    call_command(
        "video_debug",
        list=True,
        snapshot=True,
        mjpeg=True,
        stream="main",
        device="front",
        refresh_devices=True,
        auto_enable=True,
        include_inactive=True,
    )

    assert call_command_mock.call_args_list == [
        call(
            "video",
            "list",
            discover=True,
            list_streams=True,
            auto_enable=True,
            include_inactive=True,
        ),
        call(
            "video",
            "snapshot",
            device="front",
            discover=True,
            auto_enable=True,
        ),
        call(
            "video",
            "mjpeg",
            stream="main",
            include_inactive=True,
        ),
    ]


@patch("apps.video.management.commands.video_debug.call_command")
def test_video_debug_list_defaults_to_stream_listing(call_command_mock):
    """Ensure default video_debug execution includes legacy stream diagnostics."""

    call_command("video_debug")

    call_command_mock.assert_called_once_with(
        "video",
        "list",
        discover=False,
        list_streams=True,
        auto_enable=False,
        include_inactive=False,
    )
