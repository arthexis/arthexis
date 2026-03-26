"""Seed the Raspberry Pi Imager suite feature."""

from django.db import migrations


FEATURE_SLUG = "raspberry-pi-imager"


def seed_raspberry_pi_imager_suite_feature(apps, schema_editor):
    """Create or update the Raspberry Pi Imager suite feature."""

    db_alias = schema_editor.connection.alias
    Feature = apps.get_model("features", "Feature")
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager).using(db_alias)
    feature_manager.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "Raspberry Pi Imager",
            "summary": (
                "Build Raspberry Pi 4B-compatible images preloaded with Arthexis bootstrap files, "
                "publishable as downloadable artifacts."
            ),
            "is_enabled": True,
            "node_feature": None,
            "admin_requirements": (
                "Admins can create, review, and distribute generated image artifacts and inspect "
                "checksums and hosted download URIs."
            ),
            "public_requirements": (
                "Operators can consume generated image files with standard flashing software and "
                "share hosted image URIs for remote deploy workflows."
            ),
            "service_requirements": (
                "CLI command creates artifacts and stores metadata including SHA-256 digest, output "
                "path, and optional hosted download URI."
            ),
            "admin_views": [
                "admin:imager_raspberrypiimageartifact_changelist",
                "admin:features_feature_changelist",
            ],
            "public_views": [],
            "service_views": [
                "manage.py imager build",
                "manage.py imager list",
            ],
            "code_locations": [
                "apps/imager/models.py",
                "apps/imager/services.py",
                "apps/imager/management/commands/imager.py",
            ],
            "protocol_coverage": {},
            "metadata": {
                "parameters": {
                    "target": "rpi-4b",
                }
            },
            "source": "mainstream",
        },
    )


def unseed_raspberry_pi_imager_suite_feature(apps, schema_editor):
    """Remove the Raspberry Pi Imager suite feature on rollback."""

    db_alias = schema_editor.connection.alias
    Feature = apps.get_model("features", "Feature")
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager).using(db_alias)
    feature_manager.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("features", "0053_seed_energy_accounts_suite_feature"),
    ]

    operations = [
        migrations.RunPython(
            seed_raspberry_pi_imager_suite_feature,
            reverse_code=unseed_raspberry_pi_imager_suite_feature,
        ),
    ]
