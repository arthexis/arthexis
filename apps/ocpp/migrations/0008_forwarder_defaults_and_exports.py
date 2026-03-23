from django.db import migrations, models


def enable_forwarders_and_exports(apps, schema_editor):
    """Defer default enablement to the checkpointed release transform pipeline."""

    del apps, schema_editor


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0007_customer_information_display_messages"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cpforwarder",
            name="enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Enable to forward eligible charge points to the remote node. "
                    "Charge points must also have Export transactions enabled."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="charger",
            name="export_transactions",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Enable to share this charge point's transactions with remote nodes "
                    "or export tools. Required for CP forwarders."
                ),
            ),
        ),
        migrations.RunPython(enable_forwarders_and_exports, migrations.RunPython.noop),
    ]
