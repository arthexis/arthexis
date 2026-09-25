import os
import subprocess
import sys

from django.conf import settings

from arthexis.settings import CELERY_BEAT_SCHEDULE


def test_ocpp_schedules_do_not_dispatch_charger_operations() -> None:
    scheduled_tasks = {
        str(schedule["task"]) for schedule in CELERY_BEAT_SCHEDULE.values()
    }

    assert not any("charger" in task for task in scheduled_tasks)


def test_event_outbox_dispatch_is_scheduled() -> None:
    assert CELERY_BEAT_SCHEDULE["events-dispatch-pending"] == {
        "task": "events.dispatch_pending",
        "schedule": 30,
    }


SELECTED_APPS = (
    "apps.base",
    "apps.cards",
    "apps.celery",
    "apps.energy",
    "apps.events",
    "apps.nodes",
    "apps.ocpp",
    "apps.sigils",
)


def test_all_selected_apps_have_static_registry_entries() -> None:
    assert set(SELECTED_APPS).issubset(settings.INSTALLED_APPS)


def test_default_allowed_host_is_zero_address() -> None:
    env = os.environ.copy()
    env.pop("ARTHEXIS_ALLOWED_HOSTS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import arthexis.settings as s; print(','.join(s.ALLOWED_HOSTS))",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.stdout.strip() == "0.0.0.0"


def test_allowed_hosts_are_read_from_comma_separated_environment() -> None:
    env = os.environ.copy()
    env["ARTHEXIS_ALLOWED_HOSTS"] = "0.0.0.0, arthexis.com"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import arthexis.settings as s; print(','.join(s.ALLOWED_HOSTS))",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.stdout.strip() == "0.0.0.0,arthexis.com"
