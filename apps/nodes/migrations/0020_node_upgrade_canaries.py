from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0019_alter_netmessage_lcd_channel_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="upgrade_canaries",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Nodes that must be running and upgraded before this node can "
                    "auto-upgrade."
                ),
                related_name="upgrade_targets",
                symmetrical=False,
                to="nodes.node",
            ),
        ),
    ]
