from django.db import migrations


RETIRED_WEB_SAMPLER_TASK_NAME = "apps.content.tasks.run_scheduled_web_samplers"



def _cleanup_retired_web_sampler_periodic_tasks(apps, schema_editor):
    del schema_editor

    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
        PeriodicTask.objects.filter(task=RETIRED_WEB_SAMPLER_TASK_NAME).delete()
    except LookupError:
        # django_celery_beat not installed or model doesn't exist
        pass



class Migration(migrations.Migration):

    dependencies = [
        ("content", "0003_initial"),
        ("django_celery_beat", "0020_googlecalendarprofile"),
    ]

    operations = [
        migrations.RunPython(
            _cleanup_retired_web_sampler_periodic_tasks,
            migrations.RunPython.noop,
        ),
    ]
