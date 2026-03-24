"""Fail upgrades that still reference the retired web sampler Celery task alias."""

from __future__ import annotations

from django.db import migrations


RETIRED_WEB_SAMPLER_TASK_PATH = "apps.content.tasks.run_scheduled_web_samplers"


def _periodic_task_model(apps):
    """Return the periodic task model for migrations and direct test imports."""

    if apps is None:
        from django_celery_beat.models import PeriodicTask

        return PeriodicTask

    return apps.get_model("django_celery_beat", "PeriodicTask")


def enforce_retired_web_sampler_task_removed(apps, schema_editor):
    """Fail fast when retired sampler task rows still exist in beat storage."""

    del schema_editor

    PeriodicTask = _periodic_task_model(apps)
    legacy_rows = list(
        PeriodicTask.objects.filter(task=RETIRED_WEB_SAMPLER_TASK_PATH)
        .order_by("name")
        .values_list("name", flat=True)
    )
    if not legacy_rows:
        return

    task_list = ", ".join(legacy_rows)
    raise RuntimeError(
        "Retired task alias apps.content.tasks.run_scheduled_web_samplers is still "
        f"configured for periodic tasks: {task_list}. Migrate each trigger to a "
        "dedicated collector task in the owning app before applying this migration."
    )


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0009_retire_web_request_samplers"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            enforce_retired_web_sampler_task_removed,
            migrations.RunPython.noop,
        ),
    ]
