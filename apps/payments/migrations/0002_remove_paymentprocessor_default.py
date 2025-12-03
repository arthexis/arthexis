from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="openpayprocessor",
            name="is_default",
        ),
        migrations.RemoveField(
            model_name="paypalprocessor",
            name="is_default",
        ),
        migrations.RemoveField(
            model_name="stripeprocessor",
            name="is_default",
        ),
    ]
