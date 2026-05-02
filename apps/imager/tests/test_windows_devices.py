"""Windows-specific imager device discovery tests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from apps.imager.services import list_block_devices


@patch("apps.imager.services.os.name", "nt")
@patch("apps.imager.services.shutil.which", return_value="powershell.exe")
@patch("apps.imager.services.subprocess.run")
def test_list_block_devices_uses_windows_physical_drives(run_mock, _which_mock) -> None:
    """Regression: Windows writer targets should be discoverable without lsblk."""

    run_mock.return_value = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(
            {
                "disks": [
                    {
                        "Number": 0,
                        "BusType": "NVMe",
                        "Size": 512000000000,
                        "IsBoot": True,
                        "IsSystem": True,
                    },
                    {
                        "Number": 3,
                        "BusType": "USB",
                        "Size": 64000000000,
                        "IsBoot": False,
                        "IsSystem": False,
                    },
                ],
                "partitions": [
                    {
                        "DiskNumber": 3,
                        "PartitionNumber": 1,
                        "DriveLetter": "E",
                        "AccessPaths": ["E:\\"],
                    }
                ],
            }
        ),
        stderr="",
    )

    devices = list_block_devices()

    assert devices[0].path == "\\\\.\\PhysicalDrive0"
    assert devices[0].protected is True
    assert devices[1].path == "\\\\.\\PhysicalDrive3"
    assert devices[1].removable is True
    assert devices[1].protected is False
    assert devices[1].mountpoints == ["E:\\"]
    assert devices[1].partitions == ["PhysicalDrive3Partition1"]
    assert run_mock.call_args.args[0][0] == "powershell.exe"
