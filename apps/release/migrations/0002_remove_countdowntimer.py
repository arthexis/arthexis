from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("release", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="CountdownTimer",
        ),
    ]
