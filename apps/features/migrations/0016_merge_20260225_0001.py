from django.db import migrations


class Migration(migrations.Migration):
    """Merge divergent feature migration heads for stable test database setup."""

    dependencies = [
        ("features", "0014_merge_20260224_1603"),
        ("features", "0015_merge_20260224_1904"),
        ("features", "0015_merge_20260224_1907"),
    ]

    operations = []
