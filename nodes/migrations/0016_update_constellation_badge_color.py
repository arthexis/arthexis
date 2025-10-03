from django.db import migrations


NEW_COLOR = "#dc3545"
OLD_COLORS = ["#daa520"]
DEFAULT_BADGE_COLOR = "#28a745"


def apply_constellation_badge_color(apps, schema_editor):
    Node = apps.get_model("nodes", "Node")
    Node.objects.filter(
        role__name="Constellation",
        badge_color__in=OLD_COLORS + ["", DEFAULT_BADGE_COLOR],
    ).update(badge_color=NEW_COLOR)


def revert_constellation_badge_color(apps, schema_editor):
    Node = apps.get_model("nodes", "Node")
    Node.objects.filter(
        role__name="Constellation",
        badge_color=NEW_COLOR,
    ).update(badge_color=OLD_COLORS[0])


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0015_netmessage_target_limit"),
    ]

    operations = [
        migrations.RunPython(
            apply_constellation_badge_color,
            revert_constellation_badge_color,
        ),
    ]
