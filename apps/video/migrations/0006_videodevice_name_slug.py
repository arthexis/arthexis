from django.db import migrations, models
from django.utils.text import slugify


def populate_videodevice_names(apps, schema_editor):
    VideoDevice = apps.get_model("video", "VideoDevice")
    default_name = "BASE (migrate)"
    for device in VideoDevice.objects.all():
        name = (device.name or "").strip()
        if not name:
            name = default_name
        slug = (device.slug or "").strip()
        if not slug:
            slug = slugify(name)
        VideoDevice.objects.filter(pk=device.pk).update(name=name, slug=slug)


class Migration(migrations.Migration):
    dependencies = [
        ("video", "0005_videodevice_capture_resolution"),
    ]

    operations = [
        migrations.AddField(
            model_name="videodevice",
            name="name",
            field=models.CharField(default="BASE (migrate)", max_length=255),
        ),
        migrations.AddField(
            model_name="videodevice",
            name="slug",
            field=models.SlugField(blank=True, default="", max_length=255),
        ),
        migrations.RunPython(populate_videodevice_names, migrations.RunPython.noop),
    ]
