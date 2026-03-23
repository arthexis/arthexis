import django.core.validators
from django.db import migrations, models
from django.utils import timezone


RETIREMENT_NOTES = (
    "Prototype runtime scaffolding retired; records retained as inert metadata."
)


def archive_existing_prototypes(apps, schema_editor):
    Prototype = apps.get_model("prototypes", "Prototype")
    Prototype.objects.filter(retired_at__isnull=True).update(
        is_active=False,
        is_runnable=False,
        retired_at=timezone.now(),
        retirement_notes=RETIREMENT_NOTES,
    )
    Prototype.objects.filter(retirement_notes="").update(retirement_notes=RETIREMENT_NOTES)


def restore_runtime_markers(apps, schema_editor):
    Prototype = apps.get_model("prototypes", "Prototype")
    Prototype.objects.update(
        is_runnable=False,
        retired_at=None,
        retirement_notes="",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("prototypes", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="prototype",
            name="is_runnable",
            field=models.BooleanField(
                default=False,
                editable=False,
                help_text="Always false. Prototype runtime scaffolding has been retired.",
            ),
        ),
        migrations.AddField(
            model_name="prototype",
            name="retired_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the prototype runtime workflow was retired for this record.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="prototype",
            name="retirement_notes",
            field=models.TextField(
                blank=True,
                help_text="Administrative notes about the retired prototype record.",
            ),
        ),
        migrations.AlterField(
            model_name="prototype",
            name="app_module",
            field=models.CharField(
                blank=True,
                help_text="Legacy hidden runtime module retained for historical reference.",
                max_length=255,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="prototype",
            name="app_label",
            field=models.CharField(
                blank=True,
                help_text="Legacy Django app label retained for historical reference.",
                max_length=100,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="prototype",
            name="cache_dir",
            field=models.CharField(
                blank=True,
                help_text="Legacy cache directory retained for historical reference.",
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="prototype",
            name="env_overrides",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Legacy environment overrides retained for historical reference.",
            ),
        ),
        migrations.AlterField(
            model_name="prototype",
            name="is_active",
            field=models.BooleanField(
                default=False,
                editable=False,
                help_text="Legacy activation flag kept only for historical compatibility.",
            ),
        ),
        migrations.AlterField(
            model_name="prototype",
            name="port",
            field=models.PositiveIntegerField(
                default=8890,
                help_text="Legacy backend port retained for historical reference.",
            ),
        ),
        migrations.AlterField(
            model_name="prototype",
            name="slug",
            field=models.SlugField(
                help_text="Stable prototype slug retained for historical reference.",
                max_length=80,
                unique=True,
                validators=[django.core.validators.RegexValidator(message='Use lowercase snake_case starting with a letter.', regex='^[a-z][a-z0-9_]*$')],
            ),
        ),
        migrations.AlterField(
            model_name="prototype",
            name="sqlite_path",
            field=models.CharField(
                blank=True,
                help_text="Legacy SQLite path retained for historical reference.",
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="prototype",
            name="sqlite_test_path",
            field=models.CharField(
                blank=True,
                help_text="Legacy test SQLite path retained for historical reference.",
                max_length=255,
            ),
        ),
        migrations.RunPython(archive_existing_prototypes, restore_runtime_markers),
    ]
