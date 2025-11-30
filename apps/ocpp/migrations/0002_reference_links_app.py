from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0001_initial"),
        ("links", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="charger",
                    name="reference",
                    field=models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        to="links.reference",
                    ),
                ),
            ],
        )
    ]
