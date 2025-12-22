from django.db import migrations
from django.db.models import Q


def remove_arthexis_self_node(apps, schema_editor):
    Node = apps.get_model("nodes", "Node")
    Node.objects.filter(
        Q(current_relation="SELF")
        & (
            Q(hostname="arthexis.com")
            | Q(network_hostname="arthexis.com")
            | Q(address="arthexis.com")
            | Q(public_endpoint="arthexis")
        )
    ).delete()


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
