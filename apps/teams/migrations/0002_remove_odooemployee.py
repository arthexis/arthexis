from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("teams", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="OdooEmployee",
        ),
    ]
