from __future__ import annotations

from django.db import migrations, models

from apps.release.constants import PACKAGE_DESCRIPTION


class Migration(migrations.Migration):
    dependencies = [
        ("release", "0007_remove_release_manager"),
    ]

    operations = [
        migrations.AlterField(
            model_name="package",
            name="description",
            field=models.CharField(
                default=PACKAGE_DESCRIPTION,
                max_length=255,
            ),
        ),
    ]
