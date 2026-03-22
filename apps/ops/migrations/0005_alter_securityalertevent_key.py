from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0004_securityalertevent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="securityalertevent",
            name="key",
            field=models.CharField(max_length=255, unique=True),
        ),
    ]
