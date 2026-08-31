from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("cards", "0004_retire_legacy_kiosk_command_templates"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="rfidcommandtemplate",
            name="cover_image_id",
        ),
    ]
