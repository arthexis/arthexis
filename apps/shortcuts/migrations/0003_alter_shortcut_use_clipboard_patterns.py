from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shortcuts", "0002_remove_recipe_bindings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="shortcut",
            name="use_clipboard_patterns",
            field=models.BooleanField(
                default=False,
                help_text="Evaluate clipboard patterns in ascending priority before the default shortcut output.",
            ),
        ),
    ]
