from django.db import migrations, models


def update_videodevice_default_name(apps, schema_editor):
    """Defer legacy default-name cleanup to the release transform pipeline."""

    del apps, schema_editor


class Migration(migrations.Migration):
    dependencies = [
        ("video", "0007_mjpegstream_thumbnails"),
    ]

    operations = [
        migrations.AlterField(
            model_name="videodevice",
            name="name",
            field=models.CharField(default="BASE", max_length=255),
        ),
        migrations.RunPython(update_videodevice_default_name, migrations.RunPython.noop),
    ]
