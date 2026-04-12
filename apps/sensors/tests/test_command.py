from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.sensors.models import UsbTracker

pytestmark = pytest.mark.django_db


def test_sensors_scan_usb_trackers_command_outputs_summary(settings, tmp_path):
    mount_root = tmp_path / "media"
    mount = mount_root / "usb-device"
    mount.mkdir(parents=True)
    match_file = mount / "data" / "marker.txt"
    match_file.parent.mkdir(parents=True)
    match_file.write_text("status=ready\n", encoding="utf-8")
    settings.USB_TRACKER_MOUNT_ROOTS = [str(mount_root)]
    UsbTracker.objects.create(
        name="Import drive",
        slug="import-drive",
        required_file_path="data/marker.txt",
        required_file_regex=r"status=ready",
    )

    output = StringIO()
    call_command("sensors", "scan-usb-trackers", stdout=output)

    assert "USB tracker scan complete" in output.getvalue()
    assert "matched=1" in output.getvalue()


def test_sensors_scan_usb_trackers_command_json_output(settings, tmp_path):
    mount_root = tmp_path / "media"
    mount_root.mkdir(parents=True)
    settings.USB_TRACKER_MOUNT_ROOTS = [str(mount_root)]
    UsbTracker.objects.create(
        name="Import drive",
        slug="import-drive",
        required_file_path="data/marker.txt",
    )

    output = StringIO()
    call_command("sensors", "scan-usb-trackers", "--json", stdout=output)

    assert output.getvalue().strip() == '{"failed": 0, "matched": 0, "scanned": 1}'
