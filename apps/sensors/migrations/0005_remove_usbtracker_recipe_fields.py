from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recipes", "0001_initial"),
        ("sensors", "0004_usbtracker"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="usbtracker",
            name="recipe",
        ),
        migrations.RemoveField(
            model_name="usbtracker",
            name="cooldown_seconds",
        ),
        migrations.RemoveField(
            model_name="usbtracker",
            name="last_match_signature",
        ),
        migrations.RemoveField(
            model_name="usbtracker",
            name="last_recipe_result",
        ),
        migrations.RemoveField(
            model_name="usbtracker",
            name="last_triggered_at",
        ),
        migrations.AlterField(
            model_name="usbtracker",
            name="required_file_regex",
            field=models.TextField(
                blank=True,
                help_text="Optional regex used to validate file contents before marking a match.",
            ),
        ),
    ]
