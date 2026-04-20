from __future__ import annotations

import logging

from apps.clocks import utils


def test_discover_clock_devices_logs_info_for_missing_i2c_bus(caplog):
    def missing_bus_scanner(_bus: int) -> str:
        raise RuntimeError("Error: Could not open file `/dev/i2c-1' or `/dev/i2c/1': No such file or directory")

    with caplog.at_level(logging.INFO):
        devices = utils.discover_clock_devices(scanner=missing_bus_scanner)

    assert devices == []
    assert "I2C scan skipped" in caplog.text
    assert "WARNING" not in caplog.text


def test_discover_clock_devices_logs_warning_for_other_runtime_errors(caplog):
    def failing_scanner(_bus: int) -> str:
        raise RuntimeError("i2cdetect timed out")

    with caplog.at_level(logging.WARNING):
        devices = utils.discover_clock_devices(scanner=failing_scanner)

    assert devices == []
    assert "I2C scan skipped" in caplog.text


def test_discover_clock_devices_logs_warning_for_permission_denied_i2c_open(caplog):
    def permission_denied_scanner(_bus: int) -> str:
        raise RuntimeError("Error: Could not open file `/dev/i2c-1': Permission denied")

    with caplog.at_level(logging.WARNING):
        devices = utils.discover_clock_devices(scanner=permission_denied_scanner)

    assert devices == []
    assert "I2C scan skipped" in caplog.text
