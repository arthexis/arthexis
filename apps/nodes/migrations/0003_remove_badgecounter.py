from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0002_remove_node_constellation_device_and_more"),
        ("counters", "0002_copy_dashboard_data"),
    ]

    operations = [
        migrations.DeleteModel(name="BadgeCounter"),
    ]
