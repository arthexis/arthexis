from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0026_charger_auto_accept_offered_certificates"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="charger",
            name="auto_accept_offered_certificates",
        ),
    ]
