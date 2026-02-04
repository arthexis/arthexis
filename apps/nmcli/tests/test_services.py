import datetime

import pytest
from django.utils import timezone

from apps.nmcli import services


def test_scan_ap_clients_parses_station_dump(monkeypatch):
    def fake_run_nmcli(args):
        args = list(args)
        if args[:4] == ["-t", "-f", "NAME,DEVICE,TYPE", "connection"]:
            return "gelectriic-ap:wlan0:wifi\n"
        if args[:4] == ["-g", "802-11-wireless.mode", "connection", "show"]:
            return "ap\n"
        raise AssertionError(f"Unexpected nmcli args: {args}")

    station_dump = """
Station 00:11:22:33:44:55 (on wlan0)
    inactive time: 10 ms
    rx bitrate: 72.2 MBit/s
    tx bitrate: 54.0 MBit/s
    signal: -40 dBm
"""

    def fake_run_iw(args):
        assert args == ["dev", "wlan0", "station", "dump"]
        return station_dump

    fixed_now = timezone.make_aware(datetime.datetime(2024, 1, 1, 0, 0, 0))
    monkeypatch.setattr(services, "_run_nmcli", fake_run_nmcli)
    monkeypatch.setattr(services, "_run_iw", fake_run_iw)
    monkeypatch.setattr(services.timezone, "now", lambda: fixed_now)

    clients, errors = services.scan_ap_clients()

    assert errors == []
    assert clients == [
        {
            "mac_address": "00:11:22:33:44:55",
            "inactive_time_ms": 10,
            "rx_bitrate_mbps": 72.2,
            "tx_bitrate_mbps": 54.0,
            "signal_dbm": -40,
            "connection_name": "gelectriic-ap",
            "interface_name": "wlan0",
            "last_seen_at": fixed_now,
        }
    ]


def test_scan_ap_clients_raises_on_nmcli_error(monkeypatch):
    def fake_run_nmcli(args):
        raise services.NMCLIScanError("nmcli failed")

    monkeypatch.setattr(services, "_run_nmcli", fake_run_nmcli)

    with pytest.raises(services.APClientScanError, match="nmcli failed"):
        services.scan_ap_clients()
