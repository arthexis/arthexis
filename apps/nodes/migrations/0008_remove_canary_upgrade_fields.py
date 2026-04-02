from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0007_node_host_instance_id_and_more"),
        ("nodes", "0007_node_ipc_scheme_node_ipc_path"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="node",
            name="upgrade_canaries",
        ),
        migrations.RemoveField(
            model_name="upgradepolicy",
            name="requires_canaries",
        ),
    ]
