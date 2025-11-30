from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("links", "0001_initial"),
        ("core", "0105_remove_product_crm_models"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.DeleteModel(name="Reference")],
        ),
        migrations.AlterField(
            model_name="rfid",
            name="reference",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional reference for this RFID.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rfids",
                to="links.reference",
            ),
        ),
    ]
