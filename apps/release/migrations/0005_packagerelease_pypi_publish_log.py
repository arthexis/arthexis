from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("release", "0004_alter_releasemanager_group_alter_releasemanager_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="packagerelease",
            name="pypi_publish_log",
            field=models.TextField(
                blank=True, default="", editable=False, verbose_name="PyPI publish log"
            ),
        ),
    ]
