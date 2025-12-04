from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0002_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="node",
            name="profile",
        ),
        migrations.DeleteModel(
            name="NodeProfile",
        ),
    ]
