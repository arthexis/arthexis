from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("netmesh", "0008_remove_nodekeymaterial_netmesh_node_single_active_key_and_more"),
    ]

    operations = [
        migrations.DeleteModel(
            name="NodeEndpoint",
        ),
        migrations.DeleteModel(
            name="NodeRelayConfig",
        ),
        migrations.DeleteModel(
            name="RelayRegion",
        ),
        migrations.DeleteModel(
            name="ServiceAdvertisement",
        ),
    ]
