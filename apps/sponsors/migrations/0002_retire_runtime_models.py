"""Retire sponsors runtime models without dropping historical tables."""

from django.db import migrations


class Migration(migrations.Migration):
    """Remove the runtime model state while preserving database tables for upgrades."""

    dependencies = [
        ("sponsors", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="sponsorshippayment",
                    name="processor_content_type",
                ),
                migrations.RemoveField(
                    model_name="sponsorshippayment",
                    name="sponsorship",
                ),
                migrations.RemoveField(
                    model_name="sponsortier",
                    name="security_groups",
                ),
                migrations.DeleteModel(name="Sponsorship"),
                migrations.DeleteModel(name="SponsorshipPayment"),
                migrations.DeleteModel(name="SponsorTier"),
            ],
        ),
    ]
