from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0010_charger_diagnostics_location_and_more"),
        (
            "ocpp",
            "0010_charger_firmware_status_charger_firmware_status_info_and_more",
        ),
        (
            "ocpp",
            "0010_charger_last_error_code_charger_last_status_and_more",
        ),
    ]

    operations = []
