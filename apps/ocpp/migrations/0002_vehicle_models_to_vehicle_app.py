from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("vehicle", "0001_initial"),
        ("ocpp", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="ElectricVehicle"),
                migrations.DeleteModel(name="EVModel"),
                migrations.DeleteModel(name="WMICode"),
                migrations.DeleteModel(name="Brand"),
            ],
        )
    ]
