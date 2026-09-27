from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cards", "0003_expand_ocpp_id_tag"),
    ]

    operations = [
        migrations.AddField(
            model_name="cardcredential",
            name="auto_learned",
            field=models.BooleanField(default=False),
        ),
    ]
