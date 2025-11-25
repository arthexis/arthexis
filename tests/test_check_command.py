import io
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.core.management import call_command  # noqa: E402


pytestmark = [
    pytest.mark.role("Terminal"),
    pytest.mark.role("Control"),
]


def _run_check_command(*args) -> str:
    buffer = io.StringIO()
    call_command("check", *args, stdout=buffer)
    return buffer.getvalue()


def test_check_lists_all_available_options():
    output = _run_check_command()

    assert "Available checks:" in output
    for alias in [
        "admin",
        "lcd",
        "lcd-diagnostics",
        "next-upgrade",
        "pypi",
        "registration-ready",
        "rfid",
        "rfid-scan",
        "time",
    ]:
        assert alias in output


def test_check_forwards_arguments_to_selected_command():
    with patch("core.management.commands.check.call_command") as mock_call:
        _run_check_command("rfid", "--uid", "abcd")

    mock_call.assert_called_once_with("check_rfid", "--uid", "abcd")
