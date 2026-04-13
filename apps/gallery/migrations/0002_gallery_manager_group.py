from django.db import migrations

def ensure_gallery_manager_group(apps, schema_editor):
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    SecurityGroup.objects.get_or_create(name="Gallery Manager")


def remove_gallery_manager_group(apps, schema_editor):
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    SecurityGroup.objects.filter(name="Gallery Manager").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("gallery", "0001_initial"),
        ("groups", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(ensure_gallery_manager_group, remove_gallery_manager_group),
    ]
