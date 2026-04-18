from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopproduct",
            name="supports_soul_seed_preload",
            field=models.BooleanField(default=False),
        ),
    ]
