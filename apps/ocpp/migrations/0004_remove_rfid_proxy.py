from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0003_charger_configuration_check_enabled_and_more"),
    ]

    operations = [
        migrations.DeleteModel(
            name="RFID",
        ),
    ]
