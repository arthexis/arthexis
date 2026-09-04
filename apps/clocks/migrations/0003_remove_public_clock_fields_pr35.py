from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("clocks", "0002_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="clockdevice",
            name="enable_public_view",
        ),
        migrations.RemoveField(
            model_name="clockdevice",
            name="public_view_slug",
        ),
    ]
