from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0006_simulator"),
        ("references", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="charger",
            name="reference",
            field=models.OneToOneField(null=True, blank=True, on_delete=models.SET_NULL, to="references.reference"),
        ),
    ]
