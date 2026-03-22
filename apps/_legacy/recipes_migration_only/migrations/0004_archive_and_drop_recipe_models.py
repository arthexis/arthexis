"""Archive and drop runtime recipe models after typed replacements ship."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("actions", "0008_remove_recipe_targets"),
        ("emails", "0012_remove_emailcollector_notification_recipe"),
        ("recipes", "0003_recipeproduct"),
        ("sensors", "0005_remove_usbtracker_recipe_fields"),
        ("shortcuts", "0002_shortcut_typed_targets"),
    ]

    operations = [
        migrations.DeleteModel(name="RecipeProduct"),
        migrations.DeleteModel(name="Recipe"),
    ]
