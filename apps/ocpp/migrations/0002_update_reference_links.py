import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("links", "0001_initial"),
        ("ocpp", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="charger",
            name="reference",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="links.reference",
            ),
        ),
    ]
