from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cards", "0002_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cardcredential",
            name="ocpp_id_tag",
            field=models.CharField(blank=True, max_length=128),
        ),
    ]
