from __future__ import annotations

import re

from django.db import migrations


def slugify(name: str) -> str:
    slug = re.sub(r"[._]+", "-", name)
    return re.sub(r"-{2,}", "-", slug)


def forward(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    for task in PeriodicTask.objects.all().order_by("pk"):
        new_name = slugify(task.name)
        if new_name == task.name:
            continue

        conflict = (
            PeriodicTask.objects.filter(name=new_name).exclude(pk=task.pk).first()
        )
        if conflict:
            related = getattr(task, "client_report_schedule", None)
            if related and getattr(conflict, "client_report_schedule", None) is None:
                related.periodic_task_id = conflict.pk
                related.save(update_fields=["periodic_task"])
            task.delete()
            continue

        task.name = new_name
        task.save(update_fields=["name"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0085_remove_manualtask"),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
