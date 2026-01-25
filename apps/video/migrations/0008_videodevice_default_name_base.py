from django.db import migrations, models
from django.utils.text import slugify


def update_videodevice_default_name(apps, schema_editor):
    VideoDevice = apps.get_model("video", "VideoDevice")
    old_name = "BASE (migrate)"
    new_name = "BASE"
    old_slug = slugify(old_name)
    new_slug = slugify(new_name)
    devices_to_update = []

    for device in VideoDevice.objects.filter(name=old_name):
        device.name = new_name
        slug = (device.slug or "").strip()
        if not slug or slug == old_slug:
            device.slug = new_slug
        devices_to_update.append(device)

    if devices_to_update:
        VideoDevice.objects.bulk_update(
            devices_to_update,
            ["name", "slug"],
            batch_size=500,
        )


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
