from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0010_noderole_acronym"),
    ]

    operations = [
        migrations.AddField(
            model_name="netmessage",
            name="expires_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="UTC timestamp after which this message should be discarded.",
            ),
        ),
    ]
