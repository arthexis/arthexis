from django.db import migrations, models

# NOTE: This migration remains as a historical cleanup for legacy installs that
# may still have scheduled clipboard polling tasks from earlier releases.


def remove_invalid_clipboard_tasks(apps, schema_editor):
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return

    # The sample_clipboard task was removed from the codebase, but we still
    # clear out legacy beat entries that reference it or related names.
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
