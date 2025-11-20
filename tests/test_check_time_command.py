from __future__ import annotations

import io
from datetime import datetime, timezone as dt_timezone
from unittest import mock

from django.core.management import call_command


def test_check_time_outputs_current_time():
    output = io.StringIO()
    current_time = datetime(2024, 5, 1, 12, 34, 56, tzinfo=dt_timezone.utc)

    with mock.patch(
        "core.management.commands.check_time.timezone.localtime",
        return_value=current_time,
    ):
        call_command("check_time", stdout=output)

    message = output.getvalue().strip()
    assert message.endswith(
        "Current server time: 2024-05-01T12:34:56+00:00"
    )
