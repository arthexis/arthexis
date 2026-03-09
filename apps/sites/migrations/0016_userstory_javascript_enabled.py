from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0015_chat_preferences"),
    ]

    operations = [
        migrations.AddField(
            model_name="userstory",
            name="javascript_enabled",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="Whether JavaScript was enabled when the feedback was submitted.",
            ),
        ),
    ]
