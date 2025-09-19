from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_invitelead_mac_address_publicwifiaccess"),
    ]

    operations = [
        migrations.AlterField(
            model_name="package",
            name="license",
            field=models.CharField(default="GPL-3.0-only", max_length=100),
        ),
    ]
