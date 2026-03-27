from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0029_seed_public_connector_pages"),
    ]

    operations = [
        migrations.AddField(
            model_name="certificaterequest",
            name="validation_details",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="certificaterequest",
            name="validation_reason_code",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
