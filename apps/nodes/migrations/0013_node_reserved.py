from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0012_upgrade_policy_custom_controls"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="reserved",
            field=models.BooleanField(
                default=False,
                help_text="Marks a peer placeholder reserved by an image build before first contact.",
            ),
        ),
    ]
