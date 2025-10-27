from django.db import migrations, models

import ocpp.models


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0023_rename_constellation_to_watchtower"),
        ("ocpp", "0030_chargerconfiguration_charger_configuration"),
    ]

    operations = [
        migrations.AddField(
            model_name="charger",
            name="node_origin",
            field=models.ForeignKey(
                blank=True,
                null=True,
                default=ocpp.models.get_default_node_origin,
                on_delete=models.deletion.SET_NULL,
                related_name="origin_chargers",
                to="nodes.node",
            ),
        ),
    ]
