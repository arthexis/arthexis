"""Seed Odoo CRM sync suite features and integration toggles."""

from django.db import migrations


SUITE_FEATURE_SLUG = "odoo-crm-sync"
DEPLOYMENT_DISCOVERY_FEATURE_SLUG = "odoo-sync-deployment-discovery"
EMPLOYEE_IMPORT_FEATURE_SLUG = "odoo-sync-employee-import"
EVERGO_USERS_FEATURE_SLUG = "odoo-sync-evergo-users"


def seed_features(apps, schema_editor):
    """Create or update Odoo CRM sync feature toggles."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")

    Feature.objects.update_or_create(
        slug=SUITE_FEATURE_SLUG,
        defaults={
            "display": "Odoo CRM Sync",
            "source": "mainstream",
            "summary": "Umbrella suite feature for Odoo-backed CRM synchronizations.",
            "is_enabled": True,
            "node_feature": None,
            "admin_requirements": (
                "Expose toggle controls for Odoo CRM sync integrations and allow "
                "operators to disable all Odoo sync jobs with one switch."
            ),
            "public_requirements": "",
            "service_requirements": (
                "All Odoo CRM sync integrations must treat this feature as a parent gate."
            ),
            "admin_views": [
                "admin:features_feature_changelist",
                "admin:odoo_odooemployee_changelist",
                "admin:odoo_odoodeployment_changelist",
            ],
            "public_views": [],
            "service_views": ["Command: python manage.py odoo"],
            "code_locations": [
                "apps/odoo/sync_features.py",
                "apps/odoo/admin.py",
                "apps/core/admin/odoo.py",
                "apps/odoo/management/commands/odoo.py",
            ],
            "protocol_coverage": {},
            "metadata": {
                "sync_integrations": [
                    DEPLOYMENT_DISCOVERY_FEATURE_SLUG,
                    EMPLOYEE_IMPORT_FEATURE_SLUG,
                    EVERGO_USERS_FEATURE_SLUG,
                ]
            },
        },
    )

    Feature.objects.update_or_create(
        slug=DEPLOYMENT_DISCOVERY_FEATURE_SLUG,
        defaults={
            "display": "Odoo Sync: Deployment Discovery",
            "source": "mainstream",
            "summary": "Control automatic/manual discovery sync of local Odoo deployments.",
            "is_enabled": True,
            "node_feature": None,
            "admin_requirements": (
                "Allow admins to discover local Odoo instances from config files when enabled."
            ),
            "public_requirements": "",
            "service_requirements": (
                "Discovery routines must be disabled when this integration toggle is off."
            ),
            "admin_views": ["admin:odoo_odoodeployment_discover"],
            "public_views": [],
            "service_views": ["apps.odoo.services.sync_odoo_deployments"],
            "code_locations": [
                "apps/odoo/admin.py",
                "apps/odoo/services.py",
                "apps/odoo/sync_features.py",
            ],
            "protocol_coverage": {},
            "metadata": {},
        },
    )

    Feature.objects.update_or_create(
        slug=EMPLOYEE_IMPORT_FEATURE_SLUG,
        defaults={
            "display": "Odoo Sync: Employee Import",
            "source": "mainstream",
            "summary": "Control pull sync that creates missing local Odoo employee profiles.",
            "is_enabled": True,
            "node_feature": None,
            "admin_requirements": "Allow the Load Employees admin action to run when enabled.",
            "public_requirements": "",
            "service_requirements": (
                "Employee pull synchronization from Odoo should be blocked when disabled."
            ),
            "admin_views": ["admin:odoo_odooemployee_load_employees"],
            "public_views": [],
            "service_views": ["apps.core.admin.odoo.OdooEmployeeAdmin._load_missing_employees"],
            "code_locations": [
                "apps/core/admin/odoo.py",
                "apps/odoo/sync_features.py",
            ],
            "protocol_coverage": {},
            "metadata": {},
        },
    )

    Feature.objects.update_or_create(
        slug=EVERGO_USERS_FEATURE_SLUG,
        defaults={
            "display": "Odoo Sync: Evergo Users",
            "source": "mainstream",
            "summary": (
                "Control push sync that creates missing Odoo users for discovered Evergo users."
            ),
            "is_enabled": True,
            "node_feature": None,
            "admin_requirements": (
                "Provide command-level control over Evergo-to-Odoo user synchronization."
            ),
            "public_requirements": "",
            "service_requirements": (
                "Management command Odoo sync routines should skip Evergo user provisioning "
                "when disabled."
            ),
            "admin_views": ["admin:odoo_odooemployee_changelist"],
            "public_views": [],
            "service_views": ["Command: python manage.py odoo --sync-evergo-users"],
            "code_locations": [
                "apps/odoo/management/commands/odoo.py",
                "apps/evergo/models/user.py",
                "apps/odoo/sync_features.py",
            ],
            "protocol_coverage": {},
            "metadata": {},
        },
    )


def unseed_features(apps, schema_editor):
    """Remove Odoo CRM sync feature toggles."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(
        slug__in=[
            SUITE_FEATURE_SLUG,
            DEPLOYMENT_DISCOVERY_FEATURE_SLUG,
            EMPLOYEE_IMPORT_FEATURE_SLUG,
            EVERGO_USERS_FEATURE_SLUG,
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0032_rename_rfid_auth_audit_suite_slug"),
    ]

    operations = [
        migrations.RunPython(seed_features, unseed_features),
    ]
