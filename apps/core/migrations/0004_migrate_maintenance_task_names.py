from __future__ import annotations

from django.db import migrations


LEGACY_TO_CANONICAL_TASK_NAMES = {
    "apps.core.tasks.poll_emails": "apps.core.tasks.maintenance._poll_emails",
    "apps.core.tasks.run_client_report_schedule": "apps.core.tasks.maintenance._run_client_report_schedule",
    "apps.core.tasks.run_release_data_transform": "apps.core.tasks.maintenance._run_release_data_transform",
    "apps.core.tasks.run_scheduled_release": "apps.core.tasks.maintenance._run_scheduled_release",
    "apps.core.tasks.maintenance.poll_emails": "apps.core.tasks.maintenance._poll_emails",
    "apps.core.tasks.maintenance.run_client_report_schedule": "apps.core.tasks.maintenance._run_client_report_schedule",
    "apps.core.tasks.maintenance.run_release_data_transform": "apps.core.tasks.maintenance._run_release_data_transform",
    "apps.core.tasks.maintenance.run_scheduled_release": "apps.core.tasks.maintenance._run_scheduled_release",
}


def migrate_maintenance_task_names(apps, schema_editor):
    del schema_editor

    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
        PeriodicTasks = apps.get_model("django_celery_beat", "PeriodicTasks")
    except LookupError:
        return

    from django.utils import timezone

    updated = False
    for legacy_name, canonical_name in LEGACY_TO_CANONICAL_TASK_NAMES.items():
        if PeriodicTask.objects.filter(task=legacy_name).update(task=canonical_name) > 0:
            updated = True

    if updated:
        PeriodicTasks.objects.update_or_create(
            ident=1,
            defaults={"last_update": timezone.now()},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_adminnotice_trigger_upgrade_permission"),
        ("django_celery_beat", "0020_googlecalendarprofile"),
    ]

    operations = [
        migrations.RunPython(
            migrate_maintenance_task_names,
            migrations.RunPython.noop,
        ),
    ]
