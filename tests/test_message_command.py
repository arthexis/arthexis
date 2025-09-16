import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.core.management import call_command
from unittest.mock import patch


def test_message_management_command_calls_netmessage_broadcast():
    with patch("nodes.management.commands.message.NetMessage.broadcast") as broadcast:
        call_command("message", "subject", "body")
    broadcast.assert_called_once_with(
        subject="subject", body="body", reach=None, seen=None
    )


def test_message_management_command_passes_optional_arguments():
    with patch("nodes.management.commands.message.NetMessage.broadcast") as broadcast:
        call_command(
            "message", "subject", "body", "--reach", "Control", "--seen", "a", "b"
        )
    broadcast.assert_called_once_with(
        subject="subject", body="body", reach="Control", seen=["a", "b"]
    )
