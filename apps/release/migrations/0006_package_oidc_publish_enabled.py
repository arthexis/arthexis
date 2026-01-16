from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("release", "0005_packagerelease_pypi_publish_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="package",
            name="oidc_publish_enabled",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Publish releases via GitHub Actions OIDC instead of direct PyPI credentials."
                ),
            ),
        ),
    ]
