from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("counters", "0004_badge_counter_badge"),
    ]

    operations = [
        migrations.DeleteModel(
            name="BadgeCounter",
        ),
    ]
