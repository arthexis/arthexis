"""Regression tests for smb management command workflows."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.smb.models import SMBPartition, SMBServer


@pytest.mark.django_db
def test_smb_configure_and_create_and_list_regression() -> None:
    """Regression: smb command should persist server and partition mappings."""

    call_command(
        "smb",
        "configure",
        "--name",
        "primary",
        "--host",
        "files.example.internal",
        "--username",
        "admin",
    )

    call_command(
        "smb",
        "create",
        "--server",
        "primary",
        "--name",
        "Finance",
        "--share",
        "finance",
        "--path",
        "/mnt/finance",
        "--device",
        "/dev/sda1",
        "--filesystem",
        "ext4",
    )

    server = SMBServer.objects.get(name="primary")
    partition = SMBPartition.objects.get(server=server, share_name="finance")

    assert server.host == "files.example.internal"
    assert partition.local_path == "/mnt/finance"

    out = StringIO()
    call_command("smb", "list", stdout=out)
    output = out.getvalue()
    assert "primary: files.example.internal:445" in output
    assert "Finance => //files.example.internal/finance [/mnt/finance]" in output


@pytest.mark.django_db
def test_smb_discover_outputs_detected_partitions_regression() -> None:
    """Regression: smb discover should print parsed lsblk partition rows."""

    fake_output = '{"blockdevices":[{"name":"sda","type":"disk"},{"name":"sda1","type":"part","fstype":"ext4","size":"1048576"}]}'
    out = StringIO()
    with patch("apps.smb.services.subprocess.run") as mock_run:
        mock_run.return_value.stdout = fake_output
        call_command("smb", "discover", stdout=out)

    assert "/dev/sda1 fs=ext4 size=1048576" in out.getvalue()
