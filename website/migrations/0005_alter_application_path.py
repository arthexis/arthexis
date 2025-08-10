from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("website", "0004_rename_app_to_application"),
    ]

    operations = [
        migrations.AlterField(
            model_name="application",
            name="path",
            field=models.CharField(
                max_length=100,
                help_text="Base path for the app, starting with /",
                blank=True,
            ),
        ),
    ]
