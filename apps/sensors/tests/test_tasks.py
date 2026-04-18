from __future__ import annotations

from pathlib import Path

import pytest

from apps.sensors.models import Thermometer, UsbTracker
from apps.sensors.tasks import sample_thermometers, scan_usb_trackers

pytestmark = pytest.mark.django_db


def test_scan_usb_trackers_records_passive_match(settings, tmp_path: Path) -> None:
    """USB tracker scans should record passive matches without executing recipes."""

    mount_root = tmp_path / "media"
    mount = mount_root / "usb-device"
    mount.mkdir(parents=True)
    match_file = mount / "data" / "marker.txt"
    match_file.parent.mkdir(parents=True)
    match_file.write_text("serial=12345\nstatus=ready\n", encoding="utf-8")
    settings.USB_TRACKER_MOUNT_ROOTS = [str(mount_root)]
    tracker = UsbTracker.objects.create(
        name="Import drive",
        slug="import-drive",
        required_file_path="data/marker.txt",
        required_file_regex=r"status=ready",
    )

    result = scan_usb_trackers()
    tracker.refresh_from_db()

    assert result == {"scanned": 1, "matched": 1, "failed": 0}
    assert tracker.last_checked_at is not None
    assert tracker.last_matched_at is not None
    assert tracker.last_match_path == str(match_file)
    assert tracker.last_error == ""


def test_scan_usb_trackers_clears_stale_match_path(settings, tmp_path: Path) -> None:
    """USB tracker scans should clear stale match paths when no device matches."""

    mount_root = tmp_path / "media"
    mount_root.mkdir(parents=True)
    settings.USB_TRACKER_MOUNT_ROOTS = [str(mount_root)]
    tracker = UsbTracker.objects.create(
        name="Import drive",
        slug="import-drive",
        required_file_path="data/marker.txt",
        last_match_path="/old/path.txt",
    )

    result = scan_usb_trackers()
    tracker.refresh_from_db()

    assert result == {"scanned": 1, "matched": 0, "failed": 0}
    assert tracker.last_checked_at is not None
    assert tracker.last_match_path == ""
    assert tracker.last_error == ""


def test_scan_usb_trackers_reports_invalid_regex(settings, tmp_path: Path) -> None:
    """Invalid tracker regexes should be surfaced in passive scan status."""

    mount_root = tmp_path / "media"
    mount = mount_root / "usb-device"
    mount.mkdir(parents=True)
    match_file = mount / "data" / "marker.txt"
    match_file.parent.mkdir(parents=True)
    match_file.write_text("serial=12345\n", encoding="utf-8")
    settings.USB_TRACKER_MOUNT_ROOTS = [str(mount_root)]
    tracker = UsbTracker.objects.create(
        name="Import drive",
        slug="import-drive",
        required_file_path="data/marker.txt",
        required_file_regex="(",
    )

    result = scan_usb_trackers()
    tracker.refresh_from_db()

    assert result == {"scanned": 1, "matched": 0, "failed": 1}
    assert "Invalid regex" in tracker.last_error
    assert tracker.last_match_path == ""


def test_sample_thermometers_prefers_i2c_source(settings, monkeypatch) -> None:
    settings.THERMOMETER_SOURCE = "i2c"
    settings.THERMOMETER_I2C_PATH_TEMPLATE = "/sys/bus/i2c/devices/{slug}/temp1_input"
    thermometer = Thermometer.objects.create(
        name="Ambient",
        slug="1-0048",
        unit="C",
        sampling_interval_seconds=60,
    )
    captured: dict[str, object] = {}

    def fake_read_temperature(*, source, w1_paths, i2c_paths):
        captured["source"] = source
        captured["w1_paths"] = w1_paths
        captured["i2c_paths"] = i2c_paths
        return 23

    monkeypatch.setattr("apps.sensors.tasks.read_temperature", fake_read_temperature)

    result = sample_thermometers()
    thermometer.refresh_from_db()

    assert result == {"sampled": 1, "skipped": 0, "failed": 0}
    assert captured["source"] == "i2c"
    assert captured["w1_paths"] == ["/sys/bus/w1/devices/1-0048/temperature"]
    assert captured["i2c_paths"] == ["/sys/bus/i2c/devices/1-0048/temp1_input"]
    assert thermometer.last_reading == 23
