from __future__ import annotations

from pathlib import Path
import os
import subprocess

import pytest
from django.conf import settings
from django.utils import timezone

from ocpp.models import Charger, Transaction


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.django_db
def test_stop_script_requires_force_for_active_sessions() -> None:
    charger = Charger.objects.create(charger_id="ACTIVE", connector_id=1)
    Transaction.objects.create(charger=charger, start_time=timezone.now())

    env = os.environ.copy()
    env["ARTHEXIS_STOP_DB_PATH"] = str(settings.DATABASES["default"]["NAME"])

    lock_dir = REPO_ROOT / "locks"
    lock_dir.mkdir(exist_ok=True)
    charging_lock = lock_dir / "charging.lck"
    charging_lock.touch()

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
    charging_lock.unlink(missing_ok=True)


@pytest.mark.django_db
def test_stop_script_ignores_transactions_without_connector() -> None:
    Transaction.objects.all().delete()
    aggregate = Charger.objects.create(charger_id="AGGREGATE")
    Transaction.objects.create(charger=aggregate, start_time=timezone.now())

    env = os.environ.copy()
    env["ARTHEXIS_STOP_DB_PATH"] = str(settings.DATABASES["default"]["NAME"])

    charging_lock = (REPO_ROOT / "locks" / "charging.lck")
    charging_lock.unlink(missing_ok=True)

    result = subprocess.run(
        ["bash", "stop.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    combined_output = result.stdout + result.stderr
    assert "Active charging sessions detected" not in combined_output


@pytest.mark.django_db
def test_stop_script_allows_stale_sessions_without_lock() -> None:
    charger = Charger.objects.create(charger_id="STALE", connector_id=2)
    Transaction.objects.create(charger=charger, start_time=timezone.now())

    env = os.environ.copy()
    env["ARTHEXIS_STOP_DB_PATH"] = str(settings.DATABASES["default"]["NAME"])

    charging_lock = (REPO_ROOT / "locks" / "charging.lck")
    charging_lock.unlink(missing_ok=True)

    result = subprocess.run(
        ["bash", "stop.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    combined_output = result.stdout + result.stderr
    assert "assuming the sessions are stale" in combined_output
