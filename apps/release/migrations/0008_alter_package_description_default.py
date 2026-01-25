from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("release", "0007_remove_release_manager"),
    ]

    operations = [
        migrations.AlterField(
            model_name="package",
            name="description",
            field=models.CharField(
                default="Energy & Power Infrastructure",
                max_length=255,
            ),
        ),
    ]
