from django.db import migrations


def resync_node_update_task(apps, schema_editor):
    Node = apps.get_model("nodes", "Node")
    db_alias = schema_editor.connection.alias

    nodes_with_celery = (
        Node.objects.using(db_alias)
        .filter(feature_assignments__feature__slug="celery-queue")
        .distinct()
    )

    for node in nodes_with_celery.iterator():
        if not getattr(node, "is_local", False):
            # ``is_local`` is a property on the real model but may not exist on
            # historical versions. Skip nodes lacking the attribute to avoid
            # attribute errors during migrations.
            continue
        if not node.is_local:
            continue
        node.sync_feature_tasks()


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0039_remove_node_nodes_node_constellation_device_unique"),
    ]

    operations = [
        migrations.RunPython(resync_node_update_task, migrations.RunPython.noop),
    ]
