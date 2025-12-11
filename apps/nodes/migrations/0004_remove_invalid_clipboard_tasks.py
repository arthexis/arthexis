from django.db import migrations, models


def remove_invalid_clipboard_tasks(apps, schema_editor):
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return

    conditions = models.Q(task="nodes.tasks.sample_clipboard") | models.Q(
        name__icontains="poll-clipboard"
    )
    PeriodicTask.objects.filter(conditions).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0003_platform"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            remove_invalid_clipboard_tasks, reverse_code=migrations.RunPython.noop
        ),
    ]
