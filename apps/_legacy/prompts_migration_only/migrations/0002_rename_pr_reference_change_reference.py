from django.db import migrations, models


class Migration(migrations.Migration):
    """Rename PR-specific prompt metadata to an optional generic change reference."""

    dependencies = [("prompts", "0001_initial")]

    operations = [
        migrations.RenameField(
            model_name="storedprompt",
            old_name="pr_reference",
            new_name="change_reference",
        ),
        migrations.AlterField(
            model_name="storedprompt",
            name="change_reference",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional reference for the related change, ticket, or external record.",
                max_length=120,
            ),
        ),
    ]
