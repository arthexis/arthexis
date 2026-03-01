"""Merge parallel features migrations for staff chat bridge and feedback ingestion."""

from django.db import migrations


class Migration(migrations.Migration):
    """Join migration branches so database setup has a single leaf node."""

    dependencies = [
        ("features", "0020_seed_staff_chat_bridge_feature"),
        ("features", "0021_seed_feedback_ingestion_feature"),
    ]

    operations = []
