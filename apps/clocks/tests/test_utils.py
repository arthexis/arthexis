from apps.clocks.utils import discover_clock_devices, parse_i2cdetect_addresses


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
