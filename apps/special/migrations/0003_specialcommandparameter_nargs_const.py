from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("special", "0002_specialcommand_command_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="specialcommandparameter",
            name="const",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="specialcommandparameter",
            name="nargs",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
