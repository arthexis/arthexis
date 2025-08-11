from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0006_node_enable_screenshot_polling_nodescreenshot_hash_and_more"),
        ("nodes", "0006_textsample_node_alter_textsample_automated"),
    ]

    operations = [
        migrations.RenameField(
            model_name="node",
            old_name="enable_clipboard_polling",
            new_name="clipboard_polling",
        ),
        migrations.RenameField(
            model_name="node",
            old_name="enable_screenshot_polling",
            new_name="screenshot_polling",
        ),
    ]
