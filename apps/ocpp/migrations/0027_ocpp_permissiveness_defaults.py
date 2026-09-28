from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0026_chargertimelineprogress"),
    ]

    operations = [
        migrations.AddField(
            model_name="charger",
            name="protocol_mode",
            field=models.CharField(
                choices=[("open", "Open"), ("restricted", "Restricted")],
                default="open",
                max_length=16,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="charger",
            name="protocol_mode",
            field=models.CharField(
                choices=[("open", "Open"), ("restricted", "Restricted")],
                default="restricted",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="charger",
            name="authorization_mode",
            field=models.CharField(
                choices=[("open", "Open"), ("restricted", "Restricted")],
                default="restricted",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="ocpppolicy",
            name="protocol_mode",
            field=models.CharField(
                choices=[("open", "Open"), ("restricted", "Restricted")],
                default="open",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="ocpppolicy",
            name="card_mode",
            field=models.CharField(
                choices=[("open", "Open"), ("restricted", "Restricted")],
                default="open",
                max_length=16,
            ),
        ),
    ]
