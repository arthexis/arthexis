from django.db import migrations, models


def populate_videodevice_names(apps, schema_editor):
    """Defer video-device normalization to the release transform pipeline."""

    del apps, schema_editor


class Migration(migrations.Migration):
    dependencies = [
        ("video", "0005_videodevice_capture_resolution"),
    ]

    operations = [
        migrations.AddField(
            model_name="videodevice",
            name="name",
            field=models.CharField(default="BASE", max_length=255),
        ),
        migrations.AddField(
            model_name="videodevice",
            name="slug",
            field=models.SlugField(blank=True, default="", max_length=255),
        ),
        migrations.RunPython(populate_videodevice_names, migrations.RunPython.noop),
    ]
