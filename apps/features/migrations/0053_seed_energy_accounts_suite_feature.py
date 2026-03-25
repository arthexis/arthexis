"""Seed the Energy Accounts suite feature."""

from django.db import migrations


FEATURE_SLUG = "energy-accounts"


def seed_energy_accounts_suite_feature(apps, schema_editor):
    """Create or update the Energy Accounts suite feature."""

    db_alias = schema_editor.connection.alias
    Feature = apps.get_model("features", "Feature")
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager).using(db_alias)
    feature_manager.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "Energy Accounts",
            "summary": (
                "Route public charging authorization through customer energy accounts, "
                "with optional credit enforcement."
            ),
            "is_enabled": True,
            "node_feature": None,
            "admin_requirements": (
                "Operators can monitor account balances and adjust whether credits are "
                "required for authorization."
            ),
            "public_requirements": (
                "Public charge-point QR flows offer account login/creation and show account "
                "session summaries."
            ),
            "service_requirements": (
                "When enabled, RFID-only authorization is bypassed in favor of account-first "
                "authorization."
            ),
            "admin_views": [
                "admin:energy_customeraccount_changelist",
                "admin:features_feature_changelist",
            ],
            "public_views": [
                "ocpp:public-connector-page",
                "ocpp:charger-page",
                "ocpp:charger-account-summary",
            ],
            "service_views": [
                "ocpp:public-connector-page-create-account",
            ],
            "code_locations": [
                "apps/ocpp/views/public.py",
                "apps/ocpp/consumers/base/rfid.py",
                "apps/ocpp/consumers/base/legacy_transactions.py",
                "apps/features/parameters.py",
            ],
            "protocol_coverage": {},
            "metadata": {
                "parameters": {
                    "energy_credits_required": "disabled",
                }
            },
            "source": "mainstream",
        },
    )


def unseed_energy_accounts_suite_feature(apps, schema_editor):
    """Remove the Energy Accounts suite feature on rollback."""

    db_alias = schema_editor.connection.alias
    Feature = apps.get_model("features", "Feature")
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager).using(db_alias)
    feature_manager.filter(slug=FEATURE_SLUG).delete()


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
