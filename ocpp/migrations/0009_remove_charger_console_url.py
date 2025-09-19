from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0008_alter_charger_connector_id_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="charger",
            name="console_url",
        ),
    ]
