from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0003_remove_customsigil"),
        ("crms", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="odoochatbridge",
                    name="profile",
                    field=models.ForeignKey(
                        help_text="Verified Odoo employee credentials used to post chat messages.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_bridges",
                        to="crms.odooprofile",
                    ),
                ),
            ],
            database_operations=[],
        )
    ]
