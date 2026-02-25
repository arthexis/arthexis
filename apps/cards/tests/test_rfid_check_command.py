from unittest.mock import patch

import pytest

from apps.cards import scanner
from apps.cards.management.commands.rfid import Command


pytestmark = pytest.mark.integration


def test_scan_sources_no_irq_uses_direct_polling_reader():
    """`scan_sources(no_irq=True)` should bypass the background IRQ queue path."""

    with (
        patch.object(scanner, "is_configured", return_value=True),
        patch.object(scanner, "start") as start_mock,
        patch.object(
            scanner,
            "read_rfid",
            return_value={"rfid": None, "label_id": None},
        ) as read_mock,
        patch.object(scanner, "get_next_tag") as queue_mock,
    ):
        result = scanner.scan_sources(timeout=0.1, no_irq=True)

    assert result["service_mode"] == "on-demand"
    assert result["rfid"] is None
    start_mock.assert_not_called()
    queue_mock.assert_not_called()
    read_mock.assert_called_once()
    assert read_mock.call_args.kwargs.get("use_irq") is False
    assert read_mock.call_args.kwargs.get("timeout", 0) <= 0.1


@pytest.mark.parametrize("service_is_available", [True, False])
def test_rfid_check_scan_no_irq_forces_local_polling(service_is_available):
    """`rfid check --scan --no-irq` should force local polling regardless of service availability."""

    command = Command()
    with (
        patch("apps.cards.management.commands.rfid.service_available", return_value=service_is_available),
        patch.object(command, "_scan_via_attempt") as attempt_scan_mock,
        patch.object(
            command,
            "_scan_via_local",
            return_value={"rfid": "A1B2C3D4", "label_id": 10},
        ) as local_scan_mock,
    ):
        result = command._scan({"timeout": 0.5, "no_irq": True})

    assert result["rfid"] == "A1B2C3D4"
    attempt_scan_mock.assert_not_called()
    local_scan_mock.assert_called_once()
    assert local_scan_mock.call_args.kwargs.get("no_irq") is True
