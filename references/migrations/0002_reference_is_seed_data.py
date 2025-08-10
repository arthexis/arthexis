from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("references", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="reference",
            name="is_seed_data",
            field=models.BooleanField(default=False),
        ),
    ]
