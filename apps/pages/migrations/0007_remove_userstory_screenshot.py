from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0005_move_favorite_to_locals"),
        ("pages", "0006_update_experience_reference"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="userstory",
            name="screenshot",
        ),
        migrations.RemoveField(
            model_name="userstory",
            name="take_screenshot",
        ),
    ]
