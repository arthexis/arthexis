from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tests", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="suitetest",
            options={
                "ordering": [
                    "app_label",
                    "module_path",
                    "class_name",
                    "name",
                    "node_id",
                ],
                "verbose_name": "Suite test",
                "verbose_name_plural": "Suite Tests",
            },
        ),
    ]
