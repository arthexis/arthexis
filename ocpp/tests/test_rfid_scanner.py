"""Tests for :mod:`ocpp.rfid.scanner`."""

import os
from unittest.mock import patch

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from ..rfid import scanner
from core.models import RFID


@pytest.mark.parametrize(
    "configured, queued_tag, expected",
    [
        (False, {"rfid": "123", "label_id": "abc"}, {"rfid": None, "label_id": None}),
        (
            True,
            {"rfid": "123", "label_id": "abc"},
            {"rfid": "123", "label_id": "abc", "endianness": RFID.BIG_ENDIAN},
        ),
        (True, {"error": "timeout"}, {"error": "timeout"}),
        (True, None, {"rfid": None, "label_id": None}),
    ],
)
def test_scan_sources_returns_expected_payload(configured, queued_tag, expected):
    with patch("ocpp.rfid.scanner.start") as mock_start, patch(
        "ocpp.rfid.scanner.is_configured", return_value=configured
    ), patch("ocpp.rfid.scanner.get_next_tag", return_value=queued_tag):
        assert scanner.scan_sources() == expected
        mock_start.assert_called_once_with()


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
    "configured, toggled, queued_tag, expected",
    [
        (
            True,
            True,
            {"rfid": "123", "label_id": "abc", "deep_read": True},
            {
                "status": "deep read enabled",
                "enabled": True,
                "scan": {"rfid": "123", "label_id": "abc", "deep_read": True},
            },
        ),
        (
            True,
            False,
            {"rfid": "123", "label_id": "abc"},
            {"status": "deep read disabled", "enabled": False},
        ),
        (False, True, {"rfid": "123"}, {"error": "no scanner available"}),
    ],
)
def test_enable_deep_read_mode(configured, toggled, queued_tag, expected):
    with patch("ocpp.rfid.scanner.start") as mock_start, patch(
        "ocpp.rfid.scanner.is_configured", return_value=configured
    ), patch("ocpp.rfid.scanner.toggle_deep_read", return_value=toggled) as mock_toggle, patch(
        "ocpp.rfid.scanner.get_next_tag", return_value=queued_tag
    ) as mock_get_next:
        result = scanner.enable_deep_read_mode()
    mock_start.assert_called_once_with()
    if not configured:
        mock_toggle.assert_not_called()
        mock_get_next.assert_not_called()
        assert result == expected
        return

    mock_toggle.assert_called_once_with()
    if toggled:
        mock_get_next.assert_called_once_with()
    else:
        mock_get_next.assert_not_called()
    assert result == expected
