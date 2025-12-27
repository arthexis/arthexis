from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command


@patch("apps.nodes.management.commands.screenshot.save_screenshot")
@patch("apps.nodes.management.commands.screenshot.capture_screenshot")
@patch("apps.nodes.management.commands.screenshot.Node")
def test_screenshot_command_with_url(node_mock, capture_mock, save_mock, capsys):
    node_instance = object()
    node_mock.get_local.return_value = node_instance
    capture_mock.return_value = Path("/tmp/test.png")

    result = call_command("screenshot", "http://example.com")

    capture_mock.assert_called_once_with("http://example.com")
    save_mock.assert_called_once_with(
        Path("/tmp/test.png"), node=node_instance, method="COMMAND"
    )
    assert "/tmp/test.png" in capsys.readouterr().out
    assert result == "/tmp/test.png"


@patch("apps.nodes.management.commands.screenshot.save_screenshot")
@patch("apps.nodes.management.commands.screenshot.capture_screenshot")
@patch("apps.nodes.management.commands.screenshot.Node")
def test_screenshot_command_default_url(node_mock, capture_mock, save_mock):
    node_mock.get_local.return_value = SimpleNamespace(
        get_preferred_scheme=lambda: "https"
    )
    capture_mock.return_value = Path("/tmp/test.png")

    call_command("screenshot")

    capture_mock.assert_called_once_with("https://localhost:8888")
    save_mock.assert_called_once()


@patch("apps.nodes.management.commands.screenshot.time.sleep", side_effect=KeyboardInterrupt)
@patch("apps.nodes.management.commands.screenshot.save_screenshot")
@patch("apps.nodes.management.commands.screenshot.capture_screenshot")
@patch("apps.nodes.management.commands.screenshot.Node")
def test_screenshot_command_repeats_until_stopped(
    node_mock, capture_mock, save_mock, sleep_mock, capsys
):
    node_mock.get_local.return_value = None
    capture_mock.return_value = Path("/tmp/loop.png")

    result = call_command("screenshot", "http://repeat", freq=1)

    capture_mock.assert_called_once_with("http://repeat")
    save_mock.assert_called_once()
    assert "Stopping screenshot capture" in capsys.readouterr().out
    assert result == "/tmp/loop.png"


def test_screenshot_command_rejects_invalid_frequency():
    with pytest.raises(CommandError):
        call_command("screenshot", freq=0)


@patch("apps.nodes.management.commands.screenshot.save_screenshot")
@patch("apps.nodes.management.commands.screenshot.capture_local_screenshot")
@patch("apps.nodes.management.commands.screenshot.Node")
def test_screenshot_command_local_capture(node_mock, local_capture_mock, save_mock, capsys):
    node_mock.get_local.return_value = object()
    local_capture_mock.return_value = Path("/tmp/local.png")

    result = call_command("screenshot", local=True)

    local_capture_mock.assert_called_once_with()
    save_mock.assert_called_once()
    assert "/tmp/local.png" in capsys.readouterr().out
    assert result == "/tmp/local.png"


def test_screenshot_command_rejects_url_with_local():
    with pytest.raises(CommandError):
        call_command("screenshot", "http://example.com", local=True)
