from __future__ import annotations

from django.db import migrations, models


def archive_model_commands(apps, schema_editor) -> None:
    """Copy legacy model command text into the non-executable audit field."""

    del schema_editor
    LLMSummaryConfig = apps.get_model("summary", "LLMSummaryConfig")
    for config in LLMSummaryConfig.objects.exclude(model_command=""):
        config.model_command_audit = config.model_command
        config.save(update_fields=["model_command_audit"])


def restore_model_commands(apps, schema_editor) -> None:
    """Restore legacy model command text when rolling the migration back."""

    del schema_editor
    LLMSummaryConfig = apps.get_model("summary", "LLMSummaryConfig")
    for config in LLMSummaryConfig.objects.exclude(model_command_audit=""):
        config.model_command = config.model_command_audit
        config.save(update_fields=["model_command"])


class Migration(migrations.Migration):

    dependencies = [
        ("summary", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmsummaryconfig",
            name="backend",
            field=models.CharField(
                choices=[("deterministic", "Deterministic built-in summarizer")],
                default="deterministic",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="llmsummaryconfig",
            name="model_command_audit",
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(archive_model_commands, restore_model_commands),
        migrations.RemoveField(
            model_name="llmsummaryconfig",
            name="model_command",
        ),
    ]
