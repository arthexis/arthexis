from django.db import migrations


CURRENT_HEARTBEAT_TASK_PATH = "apps.core.tasks.heartbeat"
LEGACY_HEARTBEAT_TASK_PATH = "core.tasks.heartbeat"

def migrate_heartbeat_periodic_tasks(apps, schema_editor):
    """Rewrite persisted heartbeat schedules to the canonical task path."""

    del schema_editor

    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(task=LEGACY_HEARTBEAT_TASK_PATH).update(
        task=CURRENT_HEARTBEAT_TASK_PATH
    )


class Migration(migrations.Migration):

    dependencies = [
        ("celery", "0002_remove_old_periodic_tasks"),
    ]

    operations = [
        migrations.RunPython(migrate_heartbeat_periodic_tasks, migrations.RunPython.noop),
    ]
