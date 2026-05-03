from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0011_upgrade_policy_tier_channels"),
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
                    ("custom", "Custom"),
                    ("lts", "LTS"),
                    ("normal", "Normal"),
                    ("unstable", "Unstable"),
                ],
                default="stable",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="upgradepolicy",
            name="target_branch",
            field=models.CharField(
                blank=True,
                default="main",
                help_text="Git branch to inspect and pass to the upgrade command. Blank uses main.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="upgradepolicy",
            name="include_live_branch",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "For custom policies, allow same-version branch revision updates. "
                    "Built-in latest/unstable policies always use live branch tracking."
                ),
            ),
        ),
        migrations.AddField(
            model_name="upgradepolicy",
            name="allow_patch_upgrades",
            field=models.BooleanField(
                default=True,
                help_text="For custom policies, allow patch version bumps.",
            ),
        ),
        migrations.AddField(
            model_name="upgradepolicy",
            name="allow_minor_upgrades",
            field=models.BooleanField(
                default=True,
                help_text="For custom policies, allow minor version bumps.",
            ),
        ),
        migrations.AddField(
            model_name="upgradepolicy",
            name="allow_major_upgrades",
            field=models.BooleanField(
                default=False,
                help_text="For custom policies, allow major version bumps.",
            ),
        ),
    ]
