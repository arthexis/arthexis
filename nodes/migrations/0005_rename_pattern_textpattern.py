from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0004_node_enable_clipboard_polling_and_textsample"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="Pattern",
            new_name="TextPattern",
        ),
    ]
