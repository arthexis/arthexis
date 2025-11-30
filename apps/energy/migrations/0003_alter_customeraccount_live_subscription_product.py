from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("energy", "0002_initial"),
        ("crms", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="customeraccount",
                    name="live_subscription_product",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="live_subscription_accounts",
                        to="crms.product",
                    ),
                ),
            ],
            database_operations=[],
        )
    ]
