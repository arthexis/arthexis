"""Merge conflicting features app migration branches."""

from django.db import migrations


class Migration(migrations.Migration):
    """Merge migration for parallel 0011 feature branches."""

    dependencies = [
        ("features", "0011_rebrand_ocpp_ftp_server"),
        ("features", "0011_rework_evergo_api_client_feature"),
    ]

    operations = []
