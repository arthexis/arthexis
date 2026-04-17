from django.db import migrations


AP_USER_GROUP_NAME = "AP User"


def seed_ap_user_group(apps, schema_editor):
    del schema_editor
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    SecurityGroup.objects.get_or_create(name=AP_USER_GROUP_NAME)


def unseed_ap_user_group(apps, schema_editor):
    del schema_editor
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    SecurityGroup.objects.filter(name=AP_USER_GROUP_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("groups", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(seed_ap_user_group, unseed_ap_user_group),
    ]
