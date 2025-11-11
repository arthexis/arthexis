from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0046_chargerlogrequest_last_status_payload_and_more"),
        ("core", "0097_location_business_model"),
        ("teams", "0017_manualtask_location_business"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="Location"),
            ]
        ),
    ]
