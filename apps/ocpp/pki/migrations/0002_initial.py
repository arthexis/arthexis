"""Compatibility bridge for the retired Certbot DNS credential migration.

This migration originally added a foreign key from CertbotCertificate to the
now-retired DNS app. CertbotCertificate itself is removed by 0003, so retaining
the cross-app dependency would make fresh installs require a deleted app.

Existing databases may already record 0002 as applied; keeping this migration
number as a no-op preserves that history while allowing clean installs to build
the migration graph without apps.dns.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("certs", "0001_initial"),
    ]

    operations = []
