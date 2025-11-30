from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0104_delete_sigilroot"),
        ("links", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="rfid",
                    name="reference",
                    field=models.ForeignKey(
                        blank=True,
                        help_text="Optional reference for this RFID.",
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="rfids",
                        to="links.reference",
                    ),
                ),
                migrations.DeleteModel(name="Reference"),
            ],
        ),
    ]
