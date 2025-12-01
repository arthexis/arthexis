import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0001_initial"),
        ("maps", "0001_initial"),
        ("energy", "0003_move_location_to_maps"),
    ]

    operations = [
        migrations.AlterField(
            model_name="charger",
            name="location",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="chargers",
                to="maps.location",
            ),
        ),
        migrations.AlterField(
            model_name="cpreservation",
            name="location",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reservations",
                to="maps.location",
                verbose_name="Location",
            ),
        ),
    ]
