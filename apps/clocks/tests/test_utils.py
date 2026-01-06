from datetime import datetime, timezone

from apps.clocks import utils
from apps.clocks.utils import (
    discover_clock_devices,
    parse_i2cdetect_addresses,
    read_hardware_clock_time,
)


def test_parse_i2cdetect_addresses_parses_hex_grid():
    sample = """
         0 1 2 3 4 5 6 7 8 9 a b c d e f
00:          -- -- -- -- -- -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
60: -- -- -- -- -- -- -- -- 68 -- -- -- -- -- -- --
"""

    addresses = parse_i2cdetect_addresses(sample)

    assert addresses == [0x68]


def test_discover_clock_devices_labels_ds3231():
    sample = """
         0 1 2 3 4 5 6 7 8 9 a b c d e f
60: -- -- -- -- -- -- -- -- 68 -- -- -- -- -- -- --
"""

    def fake_scanner(bus: int) -> str:
        assert bus == 1
        return sample

    devices = discover_clock_devices(scanner=fake_scanner)

    assert len(devices) == 1
    device = devices[0]
    assert device.bus == 1
    assert device.address == "0x68"
    assert device.description == "DS3231 RTC"
    assert sample.strip() in device.raw_info


def test_read_hardware_clock_time(monkeypatch):
    sample_output = "2024-05-01 12:34:56.000000+00:00\n"

    monkeypatch.setattr(utils.shutil, "which", lambda cmd: "/sbin/hwclock")

    class Result:
        returncode = 0
        stdout = sample_output
        stderr = ""

    monkeypatch.setattr(utils.subprocess, "run", lambda *args, **kwargs: Result())

    clock_time = read_hardware_clock_time()

    assert clock_time == datetime(2024, 5, 1, 12, 34, 56, tzinfo=timezone.utc)
