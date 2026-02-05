from django.db import migrations


HEARTBEAT_TASK_PATHS = [
    "apps.core.tasks.heartbeat",
    "core.tasks.heartbeat",
]

HEARTBEAT_TASK_NAMES = [
    "heartbeat",
]


def remove_heartbeat_periodic_tasks(apps, schema_editor):
    del schema_editor

    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name__in=HEARTBEAT_TASK_NAMES).delete()
    PeriodicTask.objects.filter(task__in=HEARTBEAT_TASK_PATHS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("celery", "0002_remove_old_periodic_tasks"),
    ]

    operations = [
        migrations.RunPython(remove_heartbeat_periodic_tasks, migrations.RunPython.noop),
    ]
