from __future__ import annotations

from pathlib import Path
import os
import subprocess

import pytest
from django.conf import settings
from django.utils import timezone

from ocpp.models import Transaction


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.django_db
def test_stop_script_requires_force_for_active_sessions() -> None:
    Transaction.objects.create(start_time=timezone.now())

    env = os.environ.copy()
    env["ARTHEXIS_STOP_DB_PATH"] = str(settings.DATABASES["default"]["NAME"])

    result = subprocess.run(
        ["bash", "stop.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    combined_output = (result.stdout + result.stderr)
    assert "Active charging sessions detected" in combined_output

    forced = subprocess.run(
        ["bash", "stop.sh", "--force"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )

    assert forced.returncode == 0
