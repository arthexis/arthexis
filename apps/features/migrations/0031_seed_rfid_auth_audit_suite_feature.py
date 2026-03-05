from django.db import migrations


RFID_AUTH_AUDIT_FEATURE_SLUG = "rfid-auth-audit"


def seed_rfid_auth_audit_suite_feature(apps, schema_editor):
    """Create or update the RFID Auth Audit feature."""

    Feature = apps.get_model("features", "Feature")
    db_alias = schema_editor.connection.alias
    Feature.objects.using(db_alias).update_or_create(
        slug=RFID_AUTH_AUDIT_FEATURE_SLUG,
        defaults={
            "display": "RFID Auth Audit",
            "summary": (
                "Controls detailed RFID authentication attempt auditing including "
                "accepted and rejected outcomes with standardized reason codes."
            ),
            "is_enabled": True,
            "source": "mainstream",
            "node_feature": None,
            "admin_requirements": (
                "RFID attempt admin pages should expose auth-origin attempt records and "
                "payload reason metadata for debugging."
            ),
            "public_requirements": "",
            "service_requirements": (
                "RFID login requests persist accepted/rejected attempt logs when enabled."
            ),
            "admin_views": ["admin:cards_rfidattempt_changelist"],
            "public_views": ["pages:rfid-login"],
            "service_views": ["rfid-login"],
            "code_locations": [
                "apps/users/backends.py",
                "apps/cards/models/rfid_attempt.py",
                "apps/core/views/auth.py",
            ],
            "protocol_coverage": {},
        },
    )


def unseed_rfid_auth_audit_suite_feature(apps, schema_editor):
    """Remove the RFID Auth Audit feature."""

    Feature = apps.get_model("features", "Feature")
    db_alias = schema_editor.connection.alias
    Feature.objects.using(db_alias).filter(slug=RFID_AUTH_AUDIT_FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0030_feature_metadata_and_operator_language_param"),
    ]

    operations = [
        migrations.RunPython(
            seed_rfid_auth_audit_suite_feature,
            unseed_rfid_auth_audit_suite_feature,
        ),
    ]
