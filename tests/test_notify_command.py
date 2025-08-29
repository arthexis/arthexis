import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.core.management import call_command
from unittest.mock import patch


def test_notify_management_command_calls_core_notify():
    with patch("core.management.commands.notify.notify") as mock_notify:
        call_command("notify", "subject", "body")
    mock_notify.assert_called_once_with(subject="subject", body="body")
