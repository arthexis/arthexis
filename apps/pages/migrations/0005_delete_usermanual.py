from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0004_delete_rolelanding"),
    ]

    operations = [
        migrations.DeleteModel(
            name="UserManual",
        ),
    ]
