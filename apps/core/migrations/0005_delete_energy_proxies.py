from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_delete_rfid"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ClientReport",
        ),
        migrations.DeleteModel(
            name="ClientReportSchedule",
        ),
        migrations.DeleteModel(
            name="CustomerAccount",
        ),
        migrations.DeleteModel(
            name="EnergyCredit",
        ),
        migrations.DeleteModel(
            name="EnergyTariff",
        ),
        migrations.DeleteModel(
            name="EnergyTransaction",
        ),
        migrations.DeleteModel(
            name="Location",
        ),
    ]
