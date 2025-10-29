from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0029_alter_favorite_options_favorite_priority"),
    ]

    operations = [
        migrations.AddField(
            model_name="userstory",
            name="language_code",
            field=models.CharField(
                blank=True,
                max_length=15,
                help_text="Language selected when the feedback was submitted.",
            ),
        ),
    ]
