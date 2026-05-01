from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0010_cleanup_retired_node_feature_slugs"),
    ]

    operations = [
        migrations.AlterField(
            model_name="upgradepolicy",
            name="channel",
            field=models.CharField(
                choices=[
                    ("stable", "Stable / LTS"),
                    ("regular", "Regular / Normal"),
                    ("latest", "Latest / Unstable"),
                    ("lts", "LTS"),
                    ("normal", "Normal"),
                    ("unstable", "Unstable"),
                ],
                default="stable",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="upgradepolicy",
            name="interval_minutes",
            field=models.PositiveIntegerField(
                default=10080,
                help_text=(
                    "How often to check for upgrades, in minutes. Channel bump "
                    "cadences still gate whether the upgrade is allowed to proceed."
                ),
            ),
        ),
    ]
