from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0002_nodescreenshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="badge_color",
            field=models.CharField(default="#28a745", max_length=7),
        ),
    ]
