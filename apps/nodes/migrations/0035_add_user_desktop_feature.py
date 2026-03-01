from django.db import migrations


def add_user_desktop_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeRole = apps.get_model("nodes", "NodeRole")

    feature, _ = NodeFeature.objects.update_or_create(
        slug="user-desktop",
        defaults={
            "display": "User Desktop",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )

    roles = NodeRole.objects.filter(name__in=["Terminal", "Control"])
    feature.roles.set(roles)


def remove_user_desktop_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug="user-desktop").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0034_merge_20260228_1838"),
    ]

    operations = [
        migrations.RunPython(add_user_desktop_feature, remove_user_desktop_feature),
    ]
