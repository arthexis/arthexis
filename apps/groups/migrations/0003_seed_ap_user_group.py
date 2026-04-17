from django.db import migrations


AP_USER_GROUP_NAME = "AP User"


def seed_ap_user_group(apps, schema_editor):
    del schema_editor
    Group = apps.get_model("auth", "Group")
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    group, _ = Group.objects.get_or_create(name=AP_USER_GROUP_NAME)
    SecurityGroup.objects.get_or_create(group_ptr_id=group.pk)


def unseed_ap_user_group(apps, schema_editor):
    del apps, schema_editor


class Migration(migrations.Migration):

    dependencies = [
        ("groups", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(seed_ap_user_group, unseed_ap_user_group),
    ]
