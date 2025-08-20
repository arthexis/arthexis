from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0017_node_base_path_node_installed_revision_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="mac_address",
            field=models.CharField(max_length=17, unique=True, null=True, blank=True),
        ),
    ]
