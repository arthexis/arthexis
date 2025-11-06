from django.db import migrations


def enable_clipboard_poll(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeRole = apps.get_model("nodes", "NodeRole")
    Node = apps.get_model("nodes", "Node")
    NodeFeatureAssignment = apps.get_model("nodes", "NodeFeatureAssignment")

    clipboard, _ = NodeFeature.objects.get_or_create(
        slug="clipboard-poll", defaults={"display": "Clipboard Poll"}
    )
    updates = []
    if not clipboard.display:
        clipboard.display = "Clipboard Poll"
        updates.append("display")
    if not clipboard.is_seed_data:
        clipboard.is_seed_data = True
        updates.append("is_seed_data")
    if updates:
        clipboard.save(update_fields=updates)

    terminal, _ = NodeRole.objects.get_or_create(
        name="Terminal",
        defaults={"description": "Single-User Research & Development"},
    )
    role_updates = []
    if not terminal.description:
        terminal.description = "Single-User Research & Development"
        role_updates.append("description")
    if not terminal.is_seed_data:
        terminal.is_seed_data = True
        role_updates.append("is_seed_data")
    if role_updates:
        terminal.save(update_fields=role_updates)

    through = NodeFeature.roles.through
    through.objects.get_or_create(
        nodefeature_id=clipboard.pk, noderole_id=terminal.pk
    )

    for node in Node.objects.filter(role_id=terminal.pk):
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=clipboard)


def disable_clipboard_poll(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeRole = apps.get_model("nodes", "NodeRole")
    NodeFeatureAssignment = apps.get_model("nodes", "NodeFeatureAssignment")

    try:
        clipboard = NodeFeature.objects.get(slug="clipboard-poll")
        terminal = NodeRole.objects.get(name="Terminal")
    except (NodeFeature.DoesNotExist, NodeRole.DoesNotExist):
        return

    through = NodeFeature.roles.through
    through.objects.filter(
        nodefeature_id=clipboard.pk, noderole_id=terminal.pk
    ).delete()

    NodeFeatureAssignment.objects.filter(
        node__role_id=terminal.pk, feature_id=clipboard.pk
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0027_remove_gway_runner_feature"),
    ]

    operations = [
        migrations.RunPython(enable_clipboard_poll, disable_clipboard_poll),
    ]
