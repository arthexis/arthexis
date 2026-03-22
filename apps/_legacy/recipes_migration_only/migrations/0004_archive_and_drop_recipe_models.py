"""Archive and drop runtime recipe models after typed replacements ship."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("recipes", "0003_recipeproduct"),
        ("shortcuts", "0002_shortcut_typed_targets"),
    ]

    operations = [
        migrations.DeleteModel(name="RecipeProduct"),
        migrations.DeleteModel(name="Recipe"),
    ]
