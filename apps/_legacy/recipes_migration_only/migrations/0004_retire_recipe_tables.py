from django.db import migrations


def rename_recipe_tables_to_retired(apps, schema_editor):
    """Rename historical recipe tables out of the active runtime namespace."""

    schema_editor.execute("ALTER TABLE recipes_recipeproduct RENAME TO recipes_recipeproduct_retired")
    schema_editor.execute("ALTER TABLE recipes_recipe RENAME TO recipes_recipe_retired")


def restore_recipe_tables_to_runtime_names(apps, schema_editor):
    """Restore the historical recipe table names during rollback."""

    schema_editor.execute("ALTER TABLE recipes_recipe_retired RENAME TO recipes_recipe")
    schema_editor.execute("ALTER TABLE recipes_recipeproduct_retired RENAME TO recipes_recipeproduct")


class Migration(migrations.Migration):

    dependencies = [
        ("recipes", "0003_recipeproduct"),
        ("sensors", "0005_remove_usbtracker_recipe"),
        ("shortcuts", "0002_remove_recipe_bindings"),
        ("actions", "0008_remove_recipe_targets"),
        ("emails", "0012_remove_emailcollector_notification_recipe"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    rename_recipe_tables_to_retired,
                    restore_recipe_tables_to_runtime_names,
                )
            ],
            state_operations=[
                migrations.DeleteModel(name="RecipeProduct"),
                migrations.DeleteModel(name="Recipe"),
            ],
        ),
    ]
