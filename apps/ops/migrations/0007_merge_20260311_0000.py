from django.db import migrations


class Migration(migrations.Migration):
    """Merge duplicate 0006 migration leaf nodes for ops app."""

    dependencies = [
        ("ops", "0006_merge_20260310_1406"),
        ("ops", "0006_merge_20260310_1506"),
    ]

    operations = []
