from django.db import migrations


CURRENT_HEARTBEAT_TASK_PATH = "apps.core.tasks.heartbeat"
HEARTBEAT_CRONTAB = {
    "minute": "*/5",
    "hour": "*",
    "day_of_week": "*",
    "day_of_month": "*",
    "month_of_year": "*",
}
HEARTBEAT_TASK_NAME = "heartbeat"
LEGACY_HEARTBEAT_TASK_PATH = "core.tasks.heartbeat"


def _periodic_task_model(apps):
    """Return the periodic task model for migrations and direct test imports."""

    if apps is None:
        from django_celery_beat.models import PeriodicTask

        return PeriodicTask

    return apps.get_model("django_celery_beat", "PeriodicTask")


def _crontab_schedule_model(apps):
    """Return the crontab schedule model for migrations and direct test imports."""

    if apps is None:
        from django_celery_beat.models import CrontabSchedule

        return CrontabSchedule

    return apps.get_model("django_celery_beat", "CrontabSchedule")


def migrate_heartbeat_periodic_tasks(apps, schema_editor):
    """Rewrite or recreate heartbeat schedules using the canonical task path."""

    del schema_editor

    PeriodicTask = _periodic_task_model(apps)
    updated_rows = PeriodicTask.objects.filter(task=LEGACY_HEARTBEAT_TASK_PATH).update(
        task=CURRENT_HEARTBEAT_TASK_PATH
    )
    if updated_rows or PeriodicTask.objects.filter(task=CURRENT_HEARTBEAT_TASK_PATH).exists():
        return

    CrontabSchedule = _crontab_schedule_model(apps)
    schedule, _ = CrontabSchedule.objects.get_or_create(**HEARTBEAT_CRONTAB)
    PeriodicTask.objects.update_or_create(
        name=HEARTBEAT_TASK_NAME,
        defaults={
            "crontab": schedule,
            "interval": None,
            "task": CURRENT_HEARTBEAT_TASK_PATH,
            "enabled": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("celery", "0003_remove_heartbeat_periodic_tasks"),
    ]

    operations = [
        migrations.RunPython(migrate_heartbeat_periodic_tasks, migrations.RunPython.noop),
    ]
