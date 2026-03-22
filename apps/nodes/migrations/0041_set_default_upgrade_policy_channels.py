from django.db import migrations
from django.db.migrations.exceptions import IrreversibleError


ROLE_DEFAULTS = {
    "Control": "Stable",
    "Watchtower": "Stable",
    "Terminal": "Stable",
}


def set_role_default_upgrade_policies(apps, schema_editor):
    """Set conservative stable defaults for primary node roles."""

    del schema_editor
    NodeRole = apps.get_model("nodes", "NodeRole")
    UpgradePolicy = apps.get_model("nodes", "UpgradePolicy")

    for role_name, policy_name in ROLE_DEFAULTS.items():
        role = NodeRole.objects.filter(name=role_name).first()
        policy = UpgradePolicy.objects.filter(name=policy_name).first()
        if role and policy:
            role.default_upgrade_policy = policy
            role.save(update_fields=["default_upgrade_policy"])


def unset_role_default_upgrade_policies(apps, schema_editor):
    """Prevent unsafe rollback that would overwrite customized defaults."""

    del apps, schema_editor
    raise IrreversibleError(
        "Migration 0041 cannot be reversed safely because prior "
        "default_upgrade_policy values are not preserved."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0040_merge_20260307_2002"),
    ]

    operations = [
        migrations.RunPython(
            set_role_default_upgrade_policies,
            unset_role_default_upgrade_policies,
        ),
    ]
