from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0003_add_charger_status_fields"),
        ("ocpp", "0003_charger_require_rfid"),
    ]

    operations = []

