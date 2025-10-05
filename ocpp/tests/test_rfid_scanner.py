"""Tests for :mod:`ocpp.rfid.scanner`."""

import os
from unittest.mock import patch

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from ..rfid import scanner


@pytest.mark.parametrize(
    "configured, queued_tag, expected",
    [
        (False, {"rfid": "123", "label_id": "abc"}, {"rfid": None, "label_id": None}),
        (True, {"rfid": "123", "label_id": "abc"}, {"rfid": "123", "label_id": "abc"}),
        (True, {"error": "timeout"}, {"error": "timeout"}),
        (True, None, {"rfid": None, "label_id": None}),
    ],
)
def test_scan_sources_returns_expected_payload(configured, queued_tag, expected):
    with patch("ocpp.rfid.scanner.is_configured", return_value=configured), patch(
        "ocpp.rfid.scanner.get_next_tag", return_value=queued_tag
    ):
        assert scanner.scan_sources() == expected


def test_restart_sources_success_path():
    with patch("ocpp.rfid.scanner.is_configured", return_value=True), patch(
        "ocpp.rfid.scanner.stop"
    ) as mock_stop, patch("ocpp.rfid.scanner.start") as mock_start, patch(
        "ocpp.rfid.scanner.get_next_tag", return_value={"rfid": "123", "label_id": "abc"}
    ):
        assert scanner.restart_sources() == {"status": "restarted"}
        mock_stop.assert_called_once_with()
        mock_start.assert_called_once_with()


@pytest.mark.parametrize("queued_tag", [{"error": "timeout"}, None])
def test_restart_sources_error_response(queued_tag):
    with patch("ocpp.rfid.scanner.is_configured", return_value=True), patch(
        "ocpp.rfid.scanner.stop"
    ) as mock_stop, patch("ocpp.rfid.scanner.start") as mock_start, patch(
        "ocpp.rfid.scanner.get_next_tag", return_value=queued_tag
    ):
        assert scanner.restart_sources() == {"error": "no scanner available"}
        mock_stop.assert_called_once_with()
        mock_start.assert_called_once_with()


def test_restart_sources_when_not_configured():
    with patch("ocpp.rfid.scanner.is_configured", return_value=False):
        assert scanner.restart_sources() == {"error": "no scanner available"}


@pytest.mark.parametrize("configured", [True, False])
def test_test_sources_handles_configuration(configured):
    with patch("ocpp.rfid.scanner.is_configured", return_value=configured), patch(
        "ocpp.rfid.scanner.check_irq_pin", return_value={"status": "ok"}
    ) as mock_check:
        result = scanner.test_sources()
    if configured:
        mock_check.assert_called_once_with()
        assert result == {"status": "ok"}
    else:
        mock_check.assert_not_called()
        assert result == {"error": "no scanner available"}


@pytest.mark.parametrize(
    "configured, toggled, expected",
    [
        (True, True, {"status": "deep read enabled", "enabled": True}),
        (True, False, {"status": "deep read disabled", "enabled": False}),
        (False, True, {"error": "no scanner available"}),
    ],
)
def test_enable_deep_read_mode(configured, toggled, expected):
    with patch("ocpp.rfid.scanner.is_configured", return_value=configured), patch(
        "ocpp.rfid.scanner.toggle_deep_read", return_value=toggled
    ) as mock_toggle:
        result = scanner.enable_deep_read_mode()
    if configured:
        mock_toggle.assert_called_once_with()
        assert result == expected
    else:
        mock_toggle.assert_not_called()
        assert result == expected
