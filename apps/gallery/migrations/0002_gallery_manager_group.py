from django.db import migrations

GALLERY_MANAGER_GROUP_NAME = "Gallery Manager"


def ensure_gallery_manager_group(apps, schema_editor):
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    SecurityGroup.objects.get_or_create(name=GALLERY_MANAGER_GROUP_NAME)


class Migration(migrations.Migration):
    dependencies = [
        ("gallery", "0001_initial"),
        ("groups", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(ensure_gallery_manager_group, migrations.RunPython.noop),
    ]
