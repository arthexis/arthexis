from django.db import migrations


class Migration(migrations.Migration):
    """Merge feature migration branches for rebrand and source updates."""

    dependencies = [
        ("features", "0011_rebrand_ocpp_ftp_server"),
        ("features", "0013_alter_feature_source"),
    ]

    operations = []
