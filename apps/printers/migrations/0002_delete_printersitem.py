from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("printers", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="PrintersItem",
        ),
    ]
