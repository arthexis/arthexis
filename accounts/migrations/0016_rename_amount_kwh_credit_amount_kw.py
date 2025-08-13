from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0015_wmicode"),
    ]

    operations = [
        migrations.RenameField(
            model_name="credit",
            old_name="amount_kwh",
            new_name="amount_kw",
        ),
    ]
