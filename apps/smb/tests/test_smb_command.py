"""Regression tests for smb management command workflows."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.smb.models import SMBPartition, SMBServer


@pytest.mark.django_db
def test_smb_configure_and_create_and_list() -> None:
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


def test_smb_server_password_field_is_encrypted() -> None:
    """Regression: SMB server credentials should use encrypted model fields."""

    password_field = SMBServer._meta.get_field("password")
    assert password_field.__class__.__name__ == "EncryptedCharField"
