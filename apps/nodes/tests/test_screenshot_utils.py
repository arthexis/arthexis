from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from apps.content.utils import capture_and_save_screenshot


@patch("apps.content.utils.save_screenshot")
@patch("apps.content.utils.capture_screenshot")
@patch("apps.nodes.models.Node")
def test_capture_and_save_screenshot_builds_default_url(
    node_mock, capture_mock, save_mock
):
    node_mock.get_local.return_value = SimpleNamespace(
        get_preferred_scheme=lambda: "https"
    )
    capture_mock.return_value = Path("/tmp/test.png")

    result = capture_and_save_screenshot()

    capture_mock.assert_called_once_with("https://localhost:8888")
    save_mock.assert_called_once_with(
        Path("/tmp/test.png"),
        node=node_mock.get_local.return_value,
        method="TASK",
    )
    assert result == Path("/tmp/test.png")


@patch("apps.nodes.models.Node")
@patch("apps.content.utils.save_screenshot")
@patch("apps.content.utils.capture_screenshot", side_effect=RuntimeError("boom"))
def test_capture_and_save_screenshot_logs_and_suppresses_errors(
    capture_mock, save_mock, node_mock
):
    logger = Mock()

    node_mock.get_local.return_value = None

    result = capture_and_save_screenshot(logger=logger, log_capture_errors=True)

    capture_mock.assert_called_once()
    logger.error.assert_called_once()
    save_mock.assert_not_called()
    assert result is None
