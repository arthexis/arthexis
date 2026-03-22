from django.db import migrations
from django.db.models import Q


def remove_arthexis_self_node(apps, schema_editor):
    """Defer legacy self-node cleanup to the existing deferred node migration task."""

    del apps, schema_editor, Q


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0008_node_nodes_node_mac_address_unique"),
    ]

    operations = [
        migrations.RunPython(
            remove_arthexis_self_node,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
