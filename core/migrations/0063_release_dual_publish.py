from django.db import migrations, models

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0062_totpdevicesettings_user_seed_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="releasemanager",
            name="secondary_pypi_url",
            field=core.fields.SigilShortAutoField(
                blank=True,
                max_length=200,
                help_text="Optional secondary repository upload endpoint. Leave blank to disable mirrored uploads.",
                verbose_name="Secondary PyPI URL",
            ),
        ),
        migrations.AddField(
            model_name="packagerelease",
            name="github_url",
            field=models.URLField(blank=True, editable=False, verbose_name="GitHub URL"),
        ),
    ]
