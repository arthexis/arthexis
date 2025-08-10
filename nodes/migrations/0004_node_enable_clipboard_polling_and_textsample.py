from django.db import migrations, models
import uuid

class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0003_node_enable_public_api_node_public_endpoint_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="enable_clipboard_polling",
            field=models.BooleanField(default=False),
        ),
        migrations.RenameModel(
            old_name="Sample",
            new_name="TextSample",
        ),
        migrations.AddField(
            model_name="textsample",
            name="name",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            model_name="textsample",
            name="automated",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterModelOptions(
            name="textsample",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Text Sample",
                "verbose_name_plural": "Text Samples",
            },
        ),
    ]
