from django.db import migrations


ROLE_BADGE_COLORS = {
    "Constellation": "#daa520",
    "Control": "#673ab7",
}
DEFAULT_BADGE_COLOR = "#28a745"


def apply_role_badge_colors(apps, schema_editor):
    Node = apps.get_model("nodes", "Node")
    for role_name, color in ROLE_BADGE_COLORS.items():
        Node.objects.filter(
            role__name=role_name,
            badge_color__in=["", DEFAULT_BADGE_COLOR],
        ).update(badge_color=color)


def revert_role_badge_colors(apps, schema_editor):
    Node = apps.get_model("nodes", "Node")
    for role_name, color in ROLE_BADGE_COLORS.items():
        Node.objects.filter(role__name=role_name, badge_color=color).update(
            badge_color=DEFAULT_BADGE_COLOR
        )


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0012_remove_postgres_nodefeature"),
    ]

    operations = [
        migrations.RunPython(apply_role_badge_colors, revert_role_badge_colors),
    ]
