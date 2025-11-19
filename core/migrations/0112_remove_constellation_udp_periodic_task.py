from django.db import migrations
from django.db.models import Q


def remove_constellation_udp_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        Q(name="constellation_udp_probe")
        | Q(task="nodes.tasks.kickstart_constellation_udp")
    ).delete()


def noop(apps, schema_editor):
    # The task entry is obsolete; no recreation is necessary on rollback.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0111_alter_clientreportschedule_periodicity"),
    ]

    operations = [
        migrations.RunPython(remove_constellation_udp_task, reverse_code=noop),
    ]
