"""Seed the Energy Accounts suite feature."""

from django.db import migrations


FEATURE_SLUG = "energy-accounts"


def seed_energy_accounts_suite_feature(apps, schema_editor):
    """Create or update the Energy Accounts suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "Energy Accounts",
            "summary": (
                "Require QR-linked user authentication and energy account mapping "
                "for charging sessions while preserving RFID fallback compatibility."
            ),
            "is_enabled": False,
            "source": "mainstream",
            "public_requirements": (
                "Public charger QR pages prompt sign in or account creation, "
                "then continue charging automatically."
            ),
            "service_requirements": (
                "Authorization resolves RFID to customer accounts and can enforce "
                "energy credits through suite parameters."
            ),
            "public_views": [
                "/ocpp/c/<uuid:slug>/",
                "/ocpp/c/<str:cid>/account/",
            ],
            "code_locations": [
                "apps.ocpp.energy_accounts",
                "apps.ocpp.views.public",
                "apps.ocpp.consumers.base.legacy_transactions",
            ],
            "metadata": {"parameters": {"credits_required": "disabled"}},
        },
    )


def unseed_energy_accounts_suite_feature(apps, schema_editor):
    """Delete the Energy Accounts suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0052_archive_llm_summary_model_command_parameter"),
    ]

    operations = [
        migrations.RunPython(
            seed_energy_accounts_suite_feature,
            reverse_code=unseed_energy_accounts_suite_feature,
        ),
    ]
