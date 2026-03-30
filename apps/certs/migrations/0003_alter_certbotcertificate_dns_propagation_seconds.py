from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("certs", "0002_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="certbotcertificate",
            name="dns_propagation_seconds",
            field=models.PositiveIntegerField(default=300),
        ),
    ]
