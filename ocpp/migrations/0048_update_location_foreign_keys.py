import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0097_location_business_model"),
        ("ocpp", "0047_move_location_to_core"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="charger",
                    name="location",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="chargers",
                        to="core.location",
                    ),
                ),
                migrations.AlterField(
                    model_name="cpreservation",
                    name="location",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reservations",
                        to="core.location",
                        verbose_name="Location",
                    ),
                ),
            ]
        )
    ]
