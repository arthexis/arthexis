from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0067_alter_package_description"),
    ]

    operations = [
        migrations.AddField(
            model_name="rfid",
            name="post_auth_command",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional command executed after successful validation.",
            ),
        ),
    ]
