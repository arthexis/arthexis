"""Validate migration safeguards for fully retired web sampler task aliases."""

from __future__ import annotations

import importlib

import pytest


pytestmark = [pytest.mark.django_db]


def test_retired_web_sampler_task_migration_allows_clean_installation():
    """Migration check should no-op when no retired task entries remain."""

    migration = importlib.import_module(
        "apps.content.migrations.0010_enforce_web_sampler_task_retirement"
    )

    migration.enforce_retired_web_sampler_task_removed(apps=None, schema_editor=None)


def test_retired_web_sampler_task_migration_fails_when_legacy_row_exists():
    """Migration check should fail fast while retired beat task rows still exist."""

    migration = importlib.import_module(
        "apps.content.migrations.0010_enforce_web_sampler_task_retirement"
    )
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    schedule = IntervalSchedule.objects.create(every=5, period=IntervalSchedule.MINUTES)
    PeriodicTask.objects.create(
        name="legacy-web-sampler",
        interval=schedule,
        task=migration.RETIRED_WEB_SAMPLER_TASK_PATH,
        enabled=True,
    )

    with pytest.raises(RuntimeError, match="legacy-web-sampler"):
        migration.enforce_retired_web_sampler_task_removed(apps=None, schema_editor=None)
