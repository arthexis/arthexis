from __future__ import annotations

import io
from datetime import timedelta
from types import SimpleNamespace
import json

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from core import system


pytestmark = pytest.mark.django_db


class DummySchedule:
    def __init__(self, now, remaining):
        self._now = now
        self._remaining = remaining

    def now(self):
        return self._now

    def maybe_make_aware(self, value):
        return value

    def remaining_estimate(self, reference):
        return self._remaining


def _enable_auto_upgrade(base_dir):
    locks = base_dir / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    locks.joinpath("auto_upgrade.lck").write_text("latest", encoding="utf-8")


def test_check_next_upgrade_reports_schedule(monkeypatch, tmp_path):
    with override_settings(BASE_DIR=str(tmp_path)):
        _enable_auto_upgrade(tmp_path)

        now = timezone.now()
        schedule = DummySchedule(now, timedelta(minutes=15))
        task = SimpleNamespace(
            enabled=True,
            start_time=None,
            last_run_at=now - timedelta(minutes=5),
            schedule=schedule,
        )

        monkeypatch.setattr(
            system, "_get_auto_upgrade_periodic_task", lambda: (task, True, "")
        )

        output = io.StringIO()
        call_command("check_next_upgrade", stdout=output)

        message = output.getvalue()
        assert "Next upgrade check" in message
        assert "in ~15 minute" in message
        assert "Previous upgrade check" in message
        assert "~5 minute" in message
        assert "Blockers: none" in message


def test_check_next_upgrade_lists_blockers(monkeypatch, tmp_path):
    with override_settings(BASE_DIR=str(tmp_path)):
        locks = tmp_path / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        locks.joinpath("auto_upgrade_skip_revisions.lck").write_text(
            "deadbeef\nfeedcafe\n",
            encoding="utf-8",
        )
        failover_payload = {
            "reason": "Auto-upgrade health check failed",
            "detail": "Health check failed",
            "revision": "cafebabe",
            "created": timezone.now().isoformat(),
        }
        locks.joinpath("auto_upgrade_failover.lck").write_text(
            json.dumps(failover_payload),
            encoding="utf-8",
        )

        monkeypatch.setattr(
            system,
            "_get_auto_upgrade_periodic_task",
            lambda: (None, False, "django-celery-beat is not installed or configured."),
        )

        output = io.StringIO()
        call_command("check_next_upgrade", stdout=output)

        message = output.getvalue()
        assert "Blocked revisions" in message
        assert "deadbeef" in message and "feedcafe" in message
        assert "Blockers detected" in message
        assert "auto_upgrade_failover" in message or "failover" in message.lower()
        assert "auto_upgrade.lck" in message
        assert "django-celery-beat" in message
