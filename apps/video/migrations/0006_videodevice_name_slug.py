import uuid

from django.db import migrations, models
from django.utils.text import slugify


def populate_videodevice_names(apps, schema_editor):
    VideoDevice = apps.get_model("video", "VideoDevice")
    default_name = "BASE"
    devices_to_update = []
    for device in VideoDevice.objects.iterator():
        name = (device.name or "").strip() or default_name
        slug = (device.slug or "").strip() or slugify(name)
        if not slug:
            slug = uuid.uuid4().hex[:12]
        if device.name != name or device.slug != slug:
            device.name = name
            device.slug = slug
            devices_to_update.append(device)
    if devices_to_update:
        VideoDevice.objects.bulk_update(
            devices_to_update,
            ["name", "slug"],
            batch_size=500,
        )


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
