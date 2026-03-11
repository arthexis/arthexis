from django.db import migrations, models


def copy_plural_to_command_name(apps, schema_editor):
    SpecialCommand = apps.get_model("special", "SpecialCommand")
    db_alias = schema_editor.connection.alias
    for command in SpecialCommand.objects.using(db_alias).all():
        command.command_name = command.plural_name
        command.save(using=db_alias, update_fields=["command_name"])


class Migration(migrations.Migration):

    dependencies = [
        ("special", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="specialcommand",
            name="command_name",
            field=models.CharField(
                default="",
                help_text="Actual Django management command name used for invocation.",
                max_length=64,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(
            copy_plural_to_command_name,
            migrations.RunPython.noop,
        ),
    ]
