from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command


@patch("apps.nodes.management.commands.screenshot.capture_and_save_screenshot")
def test_screenshot_command_with_url(helper_mock, capsys):
    helper_mock.return_value = Path("/tmp/test.png")

    result = call_command("screenshot", "http://example.com")

    helper_mock.assert_called_once_with(
        url="http://example.com", method="COMMAND", local=False
    )
    assert "/tmp/test.png" in capsys.readouterr().out
    assert result == "/tmp/test.png"


@patch("apps.nodes.management.commands.screenshot.capture_and_save_screenshot")
def test_screenshot_command_default_url(helper_mock):
    helper_mock.return_value = Path("/tmp/test.png")

    call_command("screenshot")

    helper_mock.assert_called_once_with(url=None, method="COMMAND", local=False)


@patch("apps.nodes.management.commands.screenshot.time.sleep", side_effect=KeyboardInterrupt)
@patch("apps.nodes.management.commands.screenshot.capture_and_save_screenshot")
def test_screenshot_command_repeats_until_stopped(helper_mock, sleep_mock, capsys):
    helper_mock.return_value = Path("/tmp/loop.png")

    result = call_command("screenshot", "http://repeat", freq=1)

    helper_mock.assert_called_once_with(url="http://repeat", method="COMMAND", local=False)
    assert "Stopping screenshot capture" in capsys.readouterr().out
    assert result == "/tmp/loop.png"


def test_screenshot_command_rejects_invalid_frequency():
    with pytest.raises(CommandError):
        call_command("screenshot", freq=0)


@patch("apps.nodes.management.commands.screenshot.capture_and_save_screenshot")
def test_screenshot_command_local_capture(helper_mock, capsys):
    helper_mock.return_value = Path("/tmp/local.png")

    result = call_command("screenshot", local=True)

    helper_mock.assert_called_once_with(url=None, method="COMMAND", local=True)
    assert "/tmp/local.png" in capsys.readouterr().out
    assert result == "/tmp/local.png"


def test_screenshot_command_rejects_url_with_local():
    with pytest.raises(CommandError):
        call_command("screenshot", "http://example.com", local=True)
