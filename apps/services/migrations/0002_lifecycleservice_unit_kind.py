from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("services", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="lifecycleservice",
            name="unit_kind",
            field=models.CharField(
                choices=[("service", "Service"), ("timer", "Timer")],
                default="service",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="lifecycleservice",
            name="unit_template",
            field=models.CharField(
                help_text='Systemd unit template, for example "celery-{service}" or "arthexis-usb-inventory.timer".',
                max_length=120,
            ),
        ),
    ]
